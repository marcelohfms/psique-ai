"""
One-off: reagenda consulta de Arthur Tenório Ribeiro Clark de 27/07/2026 16:30
-> 16:00 (online, Dra. Bruna), mesmo dia.

16:00 fica 30min antes do início da janela normal de segunda-feira da
Dra. Bruna (16:30-18:30, ver DOCTOR_SCHEDULES em app/google_calendar.py).
Encaixe solicitado explicitamente pela atendente via chat — por isso o
script ignora o início padrão da janela e move o horário diretamente.

Agendamento está com status "scheduled" (evento já existe no Calendar),
então usa update_event() para apenas mover o evento existente, em vez de
criar um novo (ver skill psique-calendar-sync).

Uso: uv run python scripts/_reschedule_arthur_clark_27jul_1600_encaixe.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581996503841"
PATIENT_ID = "aa7cf66e-d53a-40eb-9dcf-65e393042e88"
APPOINTMENT_ID = "ietmrl3e31m7ntcs5fv83ovrno"
DOCTOR_KEY = "bruna"
DOCTOR_LABEL = "Dra. Bruna"
PATIENT_NAME = "Arthur Tenório Ribeiro Clark"
NEW_START = datetime(2026, 7, 27, 16, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "online"
OLD_DATETIME_LABEL = "27/07/2026 às 16:30"


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import update_event

    client = await get_supabase()

    appt = (
        await client.from_("appointments")
        .select("*")
        .eq("appointment_id", APPOINTMENT_ID)
        .maybe_single()
        .execute()
    )
    if not appt.data:
        print("⚠️  Agendamento não encontrado. Abortando.")
        return
    if appt.data.get("status") != "scheduled":
        print(f"⚠️  Status inesperado ({appt.data.get('status')}). Abortando.")
        return
    if appt.data.get("patient_id") != PATIENT_ID:
        print(f"⚠️  patient_id não bate ({appt.data.get('patient_id')}). Abortando.")
        return
    if appt.data.get("start_time") != "2026-07-27T19:30:00+00:00":
        print(f"⚠️  start_time inesperado ({appt.data.get('start_time')}). Abortando.")
        return

    calendar_id = await _get_doctor_calendar_id(DOCTOR_KEY)

    # 1. Move o evento existente no Calendar (não cria um novo)
    await update_event(
        calendar_id=calendar_id,
        event_id=APPOINTMENT_ID,
        new_start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality=MODALITY,
        patient_number=PHONE,
    )
    print(f"✅ Evento {APPOINTMENT_ID} movido no Google Calendar para {NEW_START.isoformat()}")

    # 2. Atualiza o agendamento no banco
    new_end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "start_time": NEW_START.isoformat(),
        "end_time": new_end.isoformat(),
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Agendamento atualizado no banco para 27/07/2026 às 16:00")

    # 3. Loga o evento
    await log_event("appointment_rescheduled", PHONE, {
        "appointment_id": APPOINTMENT_ID,
        "new_datetime": NEW_START.isoformat(),
        "initiated_by": "clinic",
    })

    # 4. Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: {OLD_DATETIME_LABEL}\n"
        f"Novo horário: 27/07/2026 às 16:00 (encaixe — 30min antes da janela padrão)\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Alteração feita manualmente via script, a pedido da atendente.",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
