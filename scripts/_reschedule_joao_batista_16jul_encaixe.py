"""
One-off: reagenda consulta de João Batista Silva Soares De Lima de
13/07/2026 16:00 -> 16/07/2026 10:00 (online), Dr. Júlio.

16/07/2026 é feriado (Nossa Senhora do Carmo, Recife) e está bloqueado em
SCHEDULE_EXCEPTIONS para Dr. Júlio (sem atendimento no dia inteiro). Encaixe
solicitado explicitamente pela atendente, por isso o script ignora o bloqueio
de feriado e atualiza o evento diretamente (mesmo tratamento dado ao caso
Lucas Raphael, scripts/_reschedule_lucas_raphael_16jul_encaixe.py).

Agendamento está com status "scheduled" (não pending_reschedule), então o
evento antigo no Calendar ainda existe e é apenas atualizado (update_event),
em vez de criar um novo evento.

Uso: uv run python scripts/_reschedule_joao_batista_16jul_encaixe.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581982603119@s.whatsapp.net"
PATIENT_ID = "44c641e2-94be-4a20-9e8c-1bb0f92e1f5e"
APPOINTMENT_ID = "4fvot6gdapmt9erkd0r5lqb97k"
DOCTOR_KEY = "julio"
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_NAME = "João Batista Silva Soares De Lima"
NEW_START = datetime(2026, 7, 16, 10, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "online"
OLD_DATETIME_LABEL = "13/07/2026 às 16:00"


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import update_event

    client = await get_supabase()

    appt = await client.from_("appointments").select("*").eq("appointment_id", APPOINTMENT_ID).maybe_single().execute()
    if not appt.data or appt.data.get("status") != "scheduled":
        print(f"⚠️  Agendamento não está mais 'scheduled' (status atual: {appt.data.get('status') if appt.data else 'não encontrado'}). Abortando.")
        return
    if appt.data.get("patient_id") != PATIENT_ID:
        print(f"⚠️  patient_id do agendamento ({appt.data.get('patient_id')}) não bate com o esperado. Abortando.")
        return

    # 1. Atualiza evento existente no Google Calendar
    calendar_id = await _get_doctor_calendar_id(DOCTOR_KEY)
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
    print(f"✅ Google Calendar atualizado: {NEW_START.strftime('%d/%m/%Y %H:%M')} (online)")

    # 2. Atualiza o banco de dados
    new_end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "start_time": NEW_START.isoformat(),
        "end_time": new_end.isoformat(),
        "status": "scheduled",
        "modality": MODALITY,
        "reschedule_requested_at": None,
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print(f"✅ Banco atualizado: {NEW_START.strftime('%d/%m/%Y %H:%M')} (online)")

    # 3. Loga o evento
    await log_event("appointment_rescheduled", PHONE, {
        "appointment_id": APPOINTMENT_ID,
        "new_datetime": NEW_START.isoformat(),
        "initiated_by": "clinic",
    })

    # 4. Notifica paciente via WhatsApp
    from app.chatwoot import find_or_create_conversation, send_message
    first_name = PATIENT_NAME.split()[0]
    msg = (
        f"Olá, {first_name}! 😊\n\n"
        f"Sua consulta com o *{DOCTOR_LABEL}* foi reagendada para:\n"
        f"📅 *{NEW_START.strftime('%d/%m/%Y')} às {NEW_START.strftime('%H:%M')}* (online)\n\n"
        f"Qualquer dúvida, estamos à disposição! 🙏"
    )
    try:
        conv_id = await find_or_create_conversation(PHONE)
        await send_message(conv_id, msg)
        print(f"✅ Paciente notificado via WhatsApp (conv_id={conv_id})")
    except Exception as e:
        print(f"⚠️  Falha ao notificar paciente: {e}")

    # 5. Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: {OLD_DATETIME_LABEL}\n"
        f"Novo horário: {NEW_START.strftime('%d/%m/%Y às %H:%M')} (encaixe — feriado)\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Reagendamento confirmado manualmente via script (encaixe em dia de feriado).",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")


asyncio.run(main())
