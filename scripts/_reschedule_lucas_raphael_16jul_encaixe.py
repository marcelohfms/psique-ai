"""
One-off: reagenda consulta de Lucas Raphael de Oliveira Pereira e Silva de
13/07/2026 17:00 (presencial) -> 16/07/2026 18:00 (online), Dr. Júlio.

16/07/2026 é feriado (Nossa Senhora do Carmo, Recife) e está bloqueado em
SCHEDULE_EXCEPTIONS + no Google Calendar do Dr. Júlio ("🚫 Feriado — Sem
atendimento" 14:00-20:00). Encaixe solicitado explicitamente pela atendente,
por isso o script ignora o bloqueio de feriado e cria o evento diretamente.

Agendamento já estava em pending_reschedule (evento antigo já removido do
Calendar), então cria um novo evento em vez de atualizar um existente —
mesmo comportamento de app/graph/tools.py:reschedule_appointment quando
is_pending_reschedule=True.

Uso: uv run python scripts/_reschedule_lucas_raphael_16jul_encaixe.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581994357788@s.whatsapp.net"
PATIENT_ID = "33ca40e4-0bce-4f65-8473-0f27aaf4d0e5"
OLD_APPOINTMENT_ID = "tg2cd9d4oltac8hftmnmi7mcp8"
DOCTOR_KEY = "julio"
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_NAME = "Lucas Raphael de Oliveira Pereira e Silva"
NEW_START = datetime(2026, 7, 16, 18, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "online"
OLD_DATETIME_LABEL = "13/07/2026 às 17:00"


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event

    client = await get_supabase()

    # Confirma que o agendamento ainda está pending_reschedule antes de mexer
    appt = await client.from_("appointments").select("*").eq("appointment_id", OLD_APPOINTMENT_ID).maybe_single().execute()
    if not appt.data or appt.data.get("status") != "pending_reschedule":
        print(f"⚠️  Agendamento não está mais em pending_reschedule (status atual: {appt.data.get('status') if appt.data else 'não encontrado'}). Abortando.")
        return
    if appt.data.get("patient_id") != PATIENT_ID:
        print(f"⚠️  patient_id do agendamento ({appt.data.get('patient_id')}) não bate com o esperado. Abortando.")
        return

    # 1. Cria novo evento no Calendar (o antigo já foi removido quando o slot foi liberado)
    calendar_id = await _get_doctor_calendar_id(DOCTOR_KEY)
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
    print("✅ Agendamento atualizado para 16/07/2026 às 18:00 (online)")

    # 3. Loga o evento (mesmo padrão de reschedule_appointment)
    await log_event("appointment_rescheduled", PHONE, {
        "appointment_id": new_event_id,
        "new_datetime": NEW_START.isoformat(),
        "initiated_by": appt.data.get("reschedule_initiated_by") or "clinic",
    })

    # 4. Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: {OLD_DATETIME_LABEL}\n"
        f"Novo horário: 16/07/2026 às 18:00 (encaixe — feriado)\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Reagendamento confirmado manualmente via script (encaixe em dia de feriado).",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
