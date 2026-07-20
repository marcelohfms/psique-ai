"""
One-off: reagenda a consulta do Pedro Lins De Araújo de 20/07/2026 14:00 para
30/07/2026 14:00 (Dr. Júlio), sem cobrança de nova taxa de reserva. Exceção
autorizada pela clínica: a família (via Dione, assessora da mãe Liana Lins)
pediu o reagendamento em 17/07 — dentro do prazo normal — mas o pedido nunca
foi processado por causa do bug do paciente-fantasma (contato Dione ficou
vinculado a um paciente-fantasma em vez do Pedro Lins real).
Uso: uv run python scripts/_reschedule_pedro_lins_araujo_30jul.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581996647090@s.whatsapp.net"  # Gabriel, contato vinculado ao agendamento
PATIENT_ID = "150d4395-a058-466f-95cb-54bb46b5c312"
OLD_APPOINTMENT_ID = "ikklksv5giltn54at0f1nipjj0"
PATIENT_NAME = "Pedro Lins De Araújo"
DOCTOR_LABEL = "Dr. Júlio"
NEW_START = datetime(2026, 7, 30, 14, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "presencial"


async def main():
    from app.database import get_supabase, log_event
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
    print("✅ Evento antigo (20/07 14:00) removido do Calendar")

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
    print("✅ Agendamento atualizado para 30/07/2026 às 14:00 (presencial), sem nova cobrança de taxa")

    # 4. Loga o evento como iniciativa da clínica (exceção, não consome o benefício do paciente)
    await log_event("appointment_rescheduled", PHONE.replace("@s.whatsapp.net", ""), {
        "initiated_by": "clinic",
        "new_datetime": NEW_START.replace(tzinfo=None).isoformat(),
        "appointment_id": new_event_id,
        "note": (
            "Exceção manual: pedido original da família (Dione, assessora da mãe) foi feito "
            "em 17/07 dentro do prazo normal, mas travou por bug do paciente-fantasma "
            "(_looks_like_name gap). Taxa de reserva já paga em 14/07 mantida, sem nova cobrança."
        ),
    })

    # 5. Notifica clínica
    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: 20/07/2026 às 14:00\n"
        f"Novo horário: 30/07/2026 às 14:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Reagendamento manual sem cobrança de nova taxa (exceção autorizada — pedido original "
        "da família ficou preso por bug do sistema).",
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
