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

from app.database import get_events_by_type, log_event
from app.patients import consultation_reminder_contacts, own_contact_ids


# Templates Meta de pós-consulta com pedido de avaliação no Google (o link
# fica no botão do template). {{1}} = primeiro nome do paciente. A versão de
# "retorno" vai quando aquele telefone já recebeu o pedido para esse paciente.
AVALIACAO_EVENT = "avaliacao_google_sent"

_ASK_PRIMEIRA = (
    "Se você tiver um minutinho, sua avaliação seria muito bem-vinda. "
    "É rápido e ajuda bastante o nosso trabalho.\n\n"
    "Muito obrigada!"
)
_ASK_RETORNO = (
    "Passando para lembrar da avaliação no Google. Se ainda não conseguiu "
    "deixar a sua, ela ajuda bastante o nosso trabalho e ajuda também outras "
    "pessoas que buscam um cuidado com segurança.\n\n"
    "Se você já avaliou, muito obrigada! 💜"
)

# (third_party, returning) -> (template, abertura com {nome})
_POS_CONSULTA = {
    (False, False): (
        "avaliacao_google",
        "Oi, {nome}! 😊\n\nEspero que sua consulta na Psiquê tenha corrido "
        "tudo bem. Agradecemos a confiança!\n\n",
    ),
    (True, False): (
        "avaliacao_google_terceiro",
        "Oi! 😊\n\nEspero que a consulta de {nome} na Psiquê tenha corrido "
        "tudo bem. Agradecemos a confiança!\n\n",
    ),
    (False, True): (
        "avaliacao_google_retorno",
        "Oi, {nome}! 😊\n\nEspero que mais essa consulta na Psiquê tenha "
        "corrido tudo bem. Obrigada por seguir com a gente!\n\n",
    ),
    (True, True): (
        "avaliacao_google_retorno_terceiro",
        "Oi! 😊\n\nEspero que mais essa consulta de {nome} na Psiquê tenha "
        "corrido tudo bem. Obrigada por seguirem com a gente!\n\n",
    ),
}


def _pos_consulta_template(first_name: str, third_party: bool, returning: bool) -> tuple[str, str]:
    """(nome do template Meta, texto espelhado no Chatwoot)."""
    template, opening = _POS_CONSULTA[(third_party, returning)]
    ask = _ASK_RETORNO if returning else _ASK_PRIMEIRA
    return template, opening.format(nome=first_name) + ask


async def _already_asked(phone: str, patient_id: str | None) -> bool:
    events = await get_events_by_type(phone, AVALIACAO_EVENT)
    return any((e.get("metadata") or {}).get("patient_id") == patient_id for e in events)


async def send_pos_consulta(
    phone: str, first_name: str, third_party: bool = False, returning: bool = False
) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    template, content = _pos_consulta_template(first_name, third_party, returning)
    await send_template_message(
        conv_id,
        template_name=template,
        language="pt_BR",
        category="MARKETING",
        body_params={"1": first_name},
        content=content,
    )


def _should_skip_unconfirmed(appt: dict) -> bool:
    """True if a day-before reminder asked the patient to confirm and got no reply.

    The only message that asks "Consegue confirmar a presença?" is the
    day-before reminder. Appointments booked the same day they occur never
    get one, so confirmed_at can never be set for them even when the patient
    clearly attended (e.g. paid, chatted about the consultation). Only treat
    a missing confirmed_at as a no-show/cancel signal when there was actually
    a chance to confirm.
    """
    return bool(appt.get("reminder_day_before_sent_at")) and not appt.get("confirmed_at")


async def _process_pos_consulta(client, appt: dict, now_iso: str) -> None:
    patient_id = appt.get("patient_id")

    patient = appt.get("patients") or {}
    patient_name = patient.get("name") or "paciente"
    from app.utils import display_name as _dn
    first_name = _dn(patient_name) if patient_name else "paciente"

    async def _mark_sent():
        await client.from_("appointments").update({
            "pos_consulta_sent_at": now_iso,
        }).eq("id", appt["id"]).execute()

    # Alta: se esta consulta já foi classificada como alta pelo médico, não
    # manda a pós-consulta.
    appt_id = appt.get("appointment_id")
    if patient_id and appt_id:
        rr = await (
            client.from_("return_reminders")
            .select("return_interval, last_classified_appointment_id")
            .eq("patient_id", patient_id)
            .execute()
        )
        for row in (rr.data or []):
            if row.get("return_interval") == "alta" and row.get("last_classified_appointment_id") == appt_id:
                print(f"Skipping pos_consulta for patient {patient_id} — alta registrada.")
                await _mark_sent()
                return

    if _should_skip_unconfirmed(appt):
        print(f"Skipping pos_consulta for patient {patient_id} — reminder de dia anterior enviado, sem confirmação (no-show/cancel).")
        await _mark_sent()
        return

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
        return

    # Destinatários pela regra da idade (mesma do lembrete de véspera): adulto
    # com contato próprio → só o próprio; menor → quem agendou. Evita fan-out
    # cru pros contatos "consulta" (não vaza pós-consulta de adulto pra família).
    # include_inactive=False mantém o antigo "só contato ativo".
    contacts = (
        await consultation_reminder_contacts(patient_id, appt, include_inactive=False)
        if patient_id else []
    )
    if not contacts:
        print(f"Skipping pos_consulta for patient {patient_id} — sem contato de consulta.")
        await _mark_sent()
        return

    # Contato que não é do próprio paciente (pais de menor, cônjuge que agendou)
    # recebe a versão "a consulta de {nome}".
    own_ids = await own_contact_ids(patient_id)

    sent_any = False
    for contact in contacts:
        phone = contact.get("phone")
        if not phone:
            continue
        try:
            returning = await _already_asked(phone, patient_id)
            await send_pos_consulta(
                phone, first_name,
                third_party=contact.get("id") not in own_ids,
                returning=returning,
            )
            await log_event(AVALIACAO_EVENT, phone, {
                "patient_id": patient_id,
                "appointment_id": appt.get("appointment_id"),
                "returning": returning,
            })
            sent_any = True
            print(f"Message sent to {phone} for appointment {appt['appointment_id']}")
        except Exception as e:
            print(f"Failed to send message to {phone}: {e}")
    if sent_any:
        await _mark_sent()


async def main():
    from supabase import acreate_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    result = await (
        client.from_("appointments")
        .select(
            "id, appointment_id, end_time, patient_id, consultation_type, "
            "confirmed_at, reminder_day_before_sent_at, contact_id, patients(name)"
        )
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

        await _process_pos_consulta(client, appt, now_iso)
        count += 1

    print(f"Marked {count} appointment(s) as completed.")


if __name__ == "__main__":
    asyncio.run(main())
