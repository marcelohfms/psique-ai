"""
One-off: reagenda consulta de Mariana Guimarães Dos Santos de 09/06/2026 10:00 → 10/06/2026 10:00 (Dr. Júlio).
Uso: uv run python scripts/reschedule_mariana_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581995006049@s.whatsapp.net"
APPOINTMENT_ID = "go16b007j2j18h72uglgdrpu10"
NEW_START = datetime(2026, 6, 10, 10, 0, tzinfo=TZ)
SLOT_MINUTES = 60
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_NAME = "Mariana Guimarães Dos Santos"


async def main():
    from app.database import get_supabase, get_user_by_phone
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import update_event
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()

    # --- 1. Atualizar evento no Google Calendar ---
    calendar_id = await _get_doctor_calendar_id("julio")
    await update_event(
        calendar_id=calendar_id,
        event_id=APPOINTMENT_ID,
        new_start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        patient_number=PHONE,
    )
    print(f"✅ Google Calendar atualizado: {NEW_START.strftime('%d/%m/%Y %H:%M')}")

    # --- 2. Atualizar banco de dados ---
    new_end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "start_time": NEW_START.isoformat(),
        "end_time": new_end.isoformat(),
        "status": "scheduled",
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print(f"✅ Banco atualizado: status=scheduled, {NEW_START.strftime('%d/%m/%Y %H:%M')}")

    # --- 3. Notificar paciente via WhatsApp ---
    first_name = PATIENT_NAME.split()[0]
    msg = (
        f"Olá, {first_name}! 😊\n\n"
        f"Sua consulta com o *{DOCTOR_LABEL}* foi reagendada para:\n"
        f"📅 *{NEW_START.strftime('%d/%m/%Y')} às {NEW_START.strftime('%H:%M')}*\n\n"
        f"Qualquer dúvida, estamos à disposição! 🙏"
    )
    try:
        conv_id = await find_or_create_conversation(PHONE)
        await send_message(conv_id, msg)
        print(f"✅ Paciente notificado via WhatsApp (conv_id={conv_id})")
    except Exception as e:
        print(f"⚠️  Falha ao notificar paciente: {e}")

    # --- 4. Notificar clínica ---
    from app.graph.tools import _notify_clinic
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: 09/06/2026 às 10:00\n"
        f"Novo horário: {NEW_START.strftime('%d/%m/%Y às %H:%M')}\n"
        f"Médico(a): {DOCTOR_LABEL}",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada.")


if __name__ == "__main__":
    asyncio.run(main())
