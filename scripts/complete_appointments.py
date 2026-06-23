"""
Mark past appointments as 'completed' and send a post-consultation WhatsApp template.
Runs via GitHub Actions on a schedule (every hour).

Processes appointments where:
  - status = 'scheduled'
  - end_time < now() - 24h  (at least 24 hours have passed since the appointment ended)
"""
import asyncio
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.patients import get_contacts_for_patient


async def send_pos_consulta(phone: str, first_name: str) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    content = (
        f"Olá! Esperamos que a consulta de {first_name} tenha sido boa! "
        f"Aproveite para agendar a próxima — a continuidade do tratamento faz toda a diferença. "
        f"Fique à vontade para responder pelo WhatsApp quando quiser."
    )
    await send_template_message(
        conv_id,
        template_name="pos_consulta",
        language="pt_BR",
        category="MARKETING",
        body_params={"1": first_name},
        content=content,
    )


async def main():
    from supabase import acreate_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    result = await (
        client.from_("appointments")
        .select("id, appointment_id, end_time, patient_id, consultation_type, confirmed_at, patients(name)")
        .eq("status", "scheduled")
        .is_("pos_consulta_sent_at", "null")
        .lt("end_time", cutoff)
        .execute()
    )

    appointments = result.data or []
    now_iso = datetime.now(timezone.utc).isoformat()
    count = 0

    for appt in appointments:
        await (
            client.from_("appointments")
            .update({"status": "completed", "updated_at": now_iso})
            .eq("id", appt["id"])
            .execute()
        )

        # When a primeira_consulta is completed, mark the patient as returning only
        # if there are no more scheduled primeira_consulta slots remaining.
        # This handles split first consultations (two 1h slots): the flag should only
        # flip after the last slot is done, so a not-yet-booked second slot isn't
        # incorrectly priced as acompanhamento.
        patient_id = appt.get("patient_id")
        if appt.get("consultation_type") == "primeira_consulta" and patient_id:
            remaining = await (
                client.from_("appointments")
                .select("id")
                .eq("patient_id", patient_id)
                .eq("consultation_type", "primeira_consulta")
                .eq("status", "scheduled")
                .execute()
            )
            if not remaining.data:
                await (
                    client.from_("patients")
                    .update({"is_returning_patient": True})
                    .eq("id", patient_id)
                    .execute()
                )
                print(f"Marked patient {patient_id} as returning patient.")

        patient = appt.get("patients") or {}
        patient_name = patient.get("name") or "paciente"
        first_name = patient_name.split()[0] if patient_name else "paciente"

        async def _mark_sent():
            await client.from_("appointments").update({
                "pos_consulta_sent_at": now_iso,
            }).eq("id", appt["id"]).execute()

        # Skip pos_consulta if patient never confirmed attendance via WhatsApp.
        # A null confirmed_at means the patient either cancelled or simply didn't
        # respond to the day-before reminder — sending a post-consultation message
        # would be incorrect.
        if not appt.get("confirmed_at"):
            print(f"Skipping pos_consulta for patient {patient_id} — no confirmed_at (no-show/cancel).")
            await _mark_sent()
            count += 1
            continue

        # Skip if patient already has a future/ongoing appointment scheduled
        # (end_time > now catches split first consultations still in progress).
        future = await (
            client.from_("appointments")
            .select("id")
            .eq("patient_id", patient_id)
            .eq("status", "scheduled")
            .gt("end_time", now_iso)
            .limit(1)
            .execute()
        ) if patient_id else None
        if future and future.data:
            print(f"Skipping pos_consulta for patient {patient_id} — already has a future appointment.")
            await _mark_sent()
            count += 1
            continue

        # Envia pós-consulta para TODOS os contatos com role 'consulta'.
        contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
        if not contacts:
            print(f"Skipping pos_consulta for patient {patient_id} — sem contato de consulta.")
            await _mark_sent()
            count += 1
            continue

        sent_any = False
        for contact in contacts:
            phone = contact.get("phone")
            if not phone:
                continue
            try:
                await send_pos_consulta(phone, first_name)
                sent_any = True
                print(f"Message sent to {phone} for appointment {appt['appointment_id']}")
            except Exception as e:
                print(f"Failed to send message to {phone}: {e}")
        if sent_any:
            await _mark_sent()

        count += 1

    print(f"Marked {count} appointment(s) as completed.")


if __name__ == "__main__":
    asyncio.run(main())
