import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581994344760"

async def main():
    from app.database import get_supabase
    from app.google_calendar import create_event
    from app.graph.tools import _get_doctor_calendar_id
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    patient_id = "6f9970c4-22ba-42b2-8c0a-d5837fc2390e"
    start = datetime(2026, 7, 9, 19, 0, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    calendar_id = await _get_doctor_calendar_id("julio")
    print(f"Criando evento em {calendar_id} para {start}")

    event_id = await create_event(
        calendar_id=calendar_id,
        start=start,
        slot_minutes=60,
        patient_name="Maria Alice Cavalcanti Cabral De Mello",
        doctor_name="Dr. Júlio",
        modality="presencial",
        patient_email="marcelac724@gmail.com",
        patient_number=PHONE,
    )
    print(f"Evento criado: {event_id}")

    appt = await client.from_("appointments").insert({
        "appointment_id": event_id,
        "patient_id": patient_id,
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "modality": "presencial",
        "consultation_type": "acompanhamento",
        "created_at": datetime.now(TZ).isoformat(),
        "updated_at": datetime.now(TZ).isoformat(),
    }).execute()
    print("Agendamento salvo no banco:", appt.data)

asyncio.run(main())
