"""
One-off: agenda Camila Marques Brasileiro para 27/07/2026 às 14:00 (Dr. Júlio,
presencial), a pedido da atendente via nota privada:
"Eva, agende o(a) paciente CAMILA MARQUES BRASILEIRO para o dia 27/07 às 14:00
na modalidade PRESENCIAL com Dr. Júlio e continuar o fluxo de atendimento."
(events.attendant_note_received, conversation_id 357, 2026-07-27T15:05:38Z)

Paciente pediu encaixe urgente hoje à tarde; bot transferiu para humano 2x
(events.human_transfer 13:42:04 e 13:42:09). Calendário do Dr. Júlio já tinha
apenas uma nota manual da clínica ("CAMILA MARQUES BRASILEIRO-CONSULTA",
duração zero, 14:00) — sem evento real de 1h e sem registro em appointments.
Este script cria o evento formatado + o registro no banco (ver skill
psique-calendar-sync), sem sobrescrever a nota manual.

Uso: uv run python scripts/_schedule_camila_marques_brasileiro_27jul_1400_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581987516312"
PATIENT_ID = "fc926333-5d66-4293-9223-2a7cc64a26d9"
CONTACT_ID = "71226769-25bb-474a-96d0-1076cc948844"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
DOCTOR_KEY = "julio"
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_NAME = "Camila Marques Brasileiro"
PATIENT_EMAIL = "brasileiromcamila@gmail.com"
MODALITY = "presencial"
NEW_START = datetime(2026, 7, 27, 14, 0, tzinfo=TZ)
SLOT_MINUTES = 60


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event

    client = await get_supabase()

    # Guard: aborta se já existir agendamento não cancelado para essa paciente hoje.
    existing = await client.from_("appointments").select("*") \
        .eq("patient_id", PATIENT_ID) \
        .neq("status", "canceled") \
        .execute()
    if existing.data:
        print(f"⚠️  Já existe agendamento não cancelado para {PATIENT_NAME}: {existing.data}")
        return

    calendar_id = await _get_doctor_calendar_id(DOCTOR_KEY)
    print(f"Calendar ID: {calendar_id}")

    event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality=MODALITY,
        patient_email=PATIENT_EMAIL,
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {event_id}")

    end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").insert({
        "patient_id": PATIENT_ID,
        "contact_id": CONTACT_ID,
        "doctor_id": DOCTOR_ID_JULIO,
        "appointment_id": event_id,
        "start_time": NEW_START.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "modality": MODALITY,
        "booking_fee_waived": False,
    }).execute()
    print(f"✅ Agendamento salvo: 27/07/2026 às 14:00 — {PATIENT_NAME}")

    await log_event("appointment_scheduled", PHONE, {
        "appointment_id": event_id,
        "patient_id": PATIENT_ID,
        "start_time": NEW_START.isoformat(),
        "doctor": DOCTOR_LABEL,
        "modality": MODALITY,
        "initiated_by": "clinic",
        "source": "attendant_note_oneoff_script",
    })

    await _notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Data e horário: 27/07/2026 às 14:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: Presencial\n"
        f"Contato: {PHONE}\n"
        "Encaixe feito manualmente via script, a pedido da atendente.",
        phone=PHONE,
        subject=f"Agendamento realizado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada.")


if __name__ == "__main__":
    asyncio.run(main())
