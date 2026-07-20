"""
One-off: reagenda a consulta da Júlia Soares Guaranys de 29/07/2026 09:00 para
20/07/2026 11:00 (Dr. Júlio), sem cobrança de nova taxa de reserva (exceção
autorizada pela clínica, apesar de já ser a 2ª remarcação da paciente —
divergência sobre a 1ª remarcação foi contestada pela responsável em 17/07).
Uso: uv run python scripts/_reschedule_julia_guaranys_20jul.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581988054825@s.whatsapp.net"
PATIENT_ID = "8b71f253-50d7-4f81-b227-d92233a3a359"
OLD_APPOINTMENT_ID = "id3esk38qgcieivr5oe5o0soqg"
PATIENT_NAME = "Júlia Soares Guaranys"
DOCTOR_LABEL = "Dr. Júlio"
NEW_START = datetime(2026, 7, 20, 11, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "presencial"


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event, cancel_event

    client = await get_supabase()

    appt = await client.from_("appointments").select("*").eq("appointment_id", OLD_APPOINTMENT_ID).maybe_single().execute()
    if not appt.data or appt.data.get("status") != "scheduled":
        print(f"⚠️  Agendamento não está mais em status 'scheduled' (atual: {appt.data.get('status') if appt.data else 'não encontrado'}). Abortando.")
        return

    calendar_id = await _get_doctor_calendar_id("julio")

    # 1. Cancela o evento antigo no Calendar
    await cancel_event(calendar_id, OLD_APPOINTMENT_ID)
    print("✅ Evento antigo (29/07 09:00) removido do Calendar")

    # 2. Cria novo evento
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

    # 3. Atualiza o agendamento — mantém booking_fee_paid_at (fee transferida, sem nova cobrança)
    new_end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "start_time": NEW_START.isoformat(),
        "end_time": new_end.isoformat(),
        "status": "scheduled",
        "modality": MODALITY,
        "reschedule_requested_at": None,
        "reschedule_initiated_by": "clinic",
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", OLD_APPOINTMENT_ID).execute()
    print("✅ Agendamento atualizado para 20/07/2026 às 11:00 (presencial), sem nova cobrança de taxa")

    # 4. Loga o evento como iniciativa da clínica (exceção, não consome o benefício da paciente)
    from app.database import log_event
    await log_event("appointment_rescheduled", PHONE.replace("@s.whatsapp.net", ""), {
        "initiated_by": "clinic",
        "new_datetime": NEW_START.replace(tzinfo=None).isoformat(),
        "appointment_id": new_event_id,
        "note": "Exceção manual: 2ª remarcação sem cobrança, autorizada pela clínica em 17/07 após contestação da responsável.",
    })

    # 5. Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: 29/07/2026 às 09:00\n"
        f"Novo horário: 20/07/2026 às 11:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Reagendamento manual sem cobrança de nova taxa (exceção autorizada).",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
