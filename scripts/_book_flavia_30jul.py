import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581994566910@s.whatsapp.net"
PATIENT_ID = "fda47910-3b32-46a9-8a5c-1cf916bd9704"  # Flavia Souza Passos (paciente ela mesma)
CONTACT_ID = "2fab5813-eaad-4fd3-b849-20aa4605e859"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
PATIENT_NAME = "Flavia Souza Passos"
DOCTOR_LABEL = "Dr. Júlio"
NEW_START = datetime(2026, 7, 30, 16, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "presencial"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic, log_event
    from app.google_calendar import create_event, cancel_event

    client = await get_supabase()

    calendar_id = await _get_doctor_calendar_id("julio")
    end = NEW_START + timedelta(minutes=SLOT_MINUTES)

    event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality=MODALITY,
        patient_email="flaviaspassos@gmail.com",
        patient_number=PHONE,
    )
    print(f"Evento criado no Calendar: {event_id}")

    try:
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
            "booking_fee_paid_at": None,
        }).execute()
        print("Agendamento salvo em appointments")
    except Exception as e:
        print(f"Falha ao salvar agendamento, revertendo evento no Calendar: {e}")
        await cancel_event(calendar_id, event_id)
        raise

    formatted = NEW_START.strftime("%d/%m/%Y às %H:%M")
    await log_event("appointment_booked", PHONE.replace("@s.whatsapp.net", ""), {
        "doctor": "julio",
        "datetime": formatted,
        "duration_minutes": SLOT_MINUTES,
        "patient_name": PATIENT_NAME,
        "session_note": None,
    })

    await _notify_clinic(
        f"Agendamento realizado! ✅ (registro manual — bug de contato compartilhado)\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Data e horário: {formatted}\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: Presencial\n\n"
        f"📋 LEMBRETE: enviar o Termo de Compromisso para o e-mail do paciente (flaviaspassos@gmail.com).\n"
        f"Taxa de reserva de R$ 100,00 ainda pendente de pagamento.",
        phone=PHONE.replace("@s.whatsapp.net", ""),
        subject=f"Agendamento realizado — {PATIENT_NAME}",
    )
    print("Clínica notificada por e-mail")
    print(f"\nOK — consulta de {PATIENT_NAME} criada para {formatted}, presencial, com {DOCTOR_LABEL}.")
    print(f"Google Calendar event_id: {event_id}")

asyncio.run(main())
