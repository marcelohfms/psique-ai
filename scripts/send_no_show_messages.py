"""
Envia a mensagem de falta (no-show) via WhatsApp.
Roda 1x/dia via GitHub Actions.

Processa appointments com:
  - status = 'no_show'
  - no_show_message_sent_at IS NULL

Independe de quando a falta foi marcada (médico/atendente podem marcar dias
depois). Mensagem acolhedora que já avisa que a taxa de reserva foi retida
(sem aviso prévio) e que remarcar é um novo agendamento com nova taxa. O bot
NÃO tem lógica especial de no-show: qualquer novo agendamento passa pela tool
de marcação normal, sem vínculo com a falta.
"""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.patients import get_contacts_for_patient
from app.utils import display_name as _dn


async def send_no_show_message(phone: str, first_name: str) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    content = (
        f"Olá! 😊\n\n"
        f"Sentimos a falta de {first_name} na última consulta! Queremos "
        f"muito continuar cuidando de você.\n\n"
        f"Como não houve aviso com antecedência, a taxa de reserva foi retida "
        f"desta vez.\n\n"
        f"Para agendar uma nova consulta, é só responder por aqui que a gente "
        f"te ajuda!\n\n"
        f"Estamos à disposição. 💜"
    )
    await send_template_message(
        conv_id,
        template_name="no_show",
        language="pt_BR",
        category="UTILITY",
        body_params={"1": first_name},
        content=content,
    )


async def process(client) -> int:
    result = await (
        client.from_("appointments")
        .select("id, appointment_id, patient_id, status, no_show_message_sent_at, "
                "start_time, patients(name)")
        .eq("status", "no_show")
        .is_("no_show_message_sent_at", "null")
        .execute()
    )
    appointments = result.data or []
    now_iso = datetime.now(timezone.utc).isoformat()
    count = 0
    for appt in appointments:
        patient_id = appt.get("patient_id")
        name = (appt.get("patients") or {}).get("name") or "paciente"
        first_name = _dn(name) if name else "paciente"
        contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
        sent_any = False
        for contact in contacts:
            phone = contact.get("phone")
            if not phone:
                continue
            try:
                await send_no_show_message(phone, first_name)
                sent_any = True
                print(f"No-show message sent to {phone} for appt {appt['appointment_id']}")
            except Exception as e:
                print(f"Failed to send no-show message to {phone}: {e}")
        # Só marca a flag se enviou a algum contato — assim um paciente sem
        # contato de 'consulta' é retentado amanhã (mesma postura do pos_consulta).
        if sent_any:
            await (
                client.from_("appointments")
                .update({"no_show_message_sent_at": now_iso})
                .eq("id", appt["id"])
                .execute()
            )
            count += 1
    print(f"Sent {count} no-show message(s).")
    return count


async def main():
    from supabase import acreate_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)
    await process(client)


if __name__ == "__main__":
    asyncio.run(main())
