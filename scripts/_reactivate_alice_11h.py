import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
OLD_APPT_ID = "abcmv3s0lbtgf8oe8cvp0bu1mg"
NEW_START = datetime(2026, 6, 22, 11, 0, tzinfo=TZ)
SLOT_MINUTES = 60
PATIENT_NAME = "Maria Alice Zacarias De Melo Costa"
PHONE = "5587981054742@s.whatsapp.net"
DOCTOR_LABEL = "Dr. Júlio"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()
    calendar_id = await _get_doctor_calendar_id("julio")

    # Cria evento no Calendar
    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality="presencial",
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {new_event_id}")

    # Reativa appointment
    now = datetime.now(TZ)
    end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "start_time": NEW_START.isoformat(),
        "end_time": end.isoformat(),
        "booking_fee_paid_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }).eq("appointment_id", OLD_APPT_ID).execute()
    print(f"✅ Consulta reativada: 22/06/2026 11:00 | status=scheduled")

    # Confirma para a paciente
    msg = (
        "Perfeito, Angélica! 😊 Consulta confirmada:\n\n"
        f"📅 *22/06/2026 às 11:00*\n"
        f"👨‍⚕️ {DOCTOR_LABEL}\n"
        f"📍 Presencial\n\n"
        "Sua vaga está garantida! Até lá. 🩷"
    )
    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Confirmação enviada (conv_id={conv_id})")

    # Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: 22/06/2026 às 10:00 (cancelado)\n"
        f"Novo horário: 22/06/2026 às 11:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: Presencial",
        phone=PHONE,
        subject=f"Agendamento reativado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada.")

asyncio.run(main())
