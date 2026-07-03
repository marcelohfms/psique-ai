"""
One-off: agenda Ian Vittor Pinto De Sa Leitão Melo Gonzaga para 26/06/2026 às 11:00 (Dra. Bruna, presencial).
Uso: uv run python scripts/insert_ian_appt_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581988007007@s.whatsapp.net"
USER_ID = "ffb27e8f-13a9-433e-b628-efdbd6602f8e"   # registro is_patient=False (Norma → Ian)
DOCTOR_ID_BRUNA = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
PATIENT_NAME = "Ian Vittor Pinto De Sa Leitão Melo Gonzaga"
CONTACT_NAME = "Norma Cristina Pinto De Sa Leitão"
DOCTOR_LABEL = "Dra. Bruna"
NEW_START = datetime(2026, 6, 26, 11, 0, tzinfo=TZ)
SLOT_MINUTES = 60


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event

    client = await get_supabase()

    # --- 1. Criar evento no Google Calendar ---
    calendar_id = await _get_doctor_calendar_id("bruna")
    print(f"Calendar ID: {calendar_id}")

    event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality="presencial",
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {event_id}")

    # --- 2. Inserir no banco ---
    end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").insert({
        "user_id": USER_ID,
        "doctor_id": DOCTOR_ID_BRUNA,
        "appointment_id": event_id,
        "start_time": NEW_START.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "modality": "presencial",
    }).execute()
    print(f"✅ Agendamento salvo: 26/06/2026 às 11:00 — {PATIENT_NAME}")

    # --- 3. Notificar clínica ---
    await _notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Data e horário: 26/06/2026 às 11:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: Presencial\n"
        f"Contato: {CONTACT_NAME}",
        phone=PHONE,
        subject=f"Agendamento realizado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada.")


if __name__ == "__main__":
    asyncio.run(main())
