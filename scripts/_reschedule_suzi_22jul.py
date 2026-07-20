import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581996962165@s.whatsapp.net"
PATIENT_ID = "3b0a5557-bef2-4c22-b416-5f564deb7592"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
OLD_APPOINTMENT_ID = "tpkdfncsqpgso0msq55haesm2o"
PATIENT_NAME = "Suzi Monteiro Viana"
DOCTOR_LABEL = "Dr. Júlio"
NEW_START = datetime(2026, 7, 22, 9, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "presencial"


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event

    client = await get_supabase()

    # Confirma que o agendamento ainda está pending_reschedule antes de mexer
    appt = await client.from_("appointments").select("*").eq("appointment_id", OLD_APPOINTMENT_ID).maybe_single().execute()
    if not appt.data or appt.data.get("status") != "pending_reschedule":
        print(f"⚠️  Agendamento não está mais em pending_reschedule (status atual: {appt.data.get('status') if appt.data else 'não encontrado'}). Abortando.")
        return

    # 1. Cria novo evento no Calendar (o antigo já foi removido quando o slot foi liberado)
    calendar_id = await _get_doctor_calendar_id("julio")
    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality=MODALITY,
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {new_event_id}")

    # 2. Atualiza o agendamento existente para o novo horário/evento
    new_end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "start_time": NEW_START.isoformat(),
        "end_time": new_end.isoformat(),
        "status": "scheduled",
        "modality": MODALITY,
        "reschedule_requested_at": None,
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", OLD_APPOINTMENT_ID).execute()
    print("✅ Agendamento atualizado para 22/07/2026 às 09:00 (presencial)")

    # 3. Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: 02/07/2026 às 17:00\n"
        f"Novo horário: 22/07/2026 às 09:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Reagendamento confirmado manualmente via script.",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
