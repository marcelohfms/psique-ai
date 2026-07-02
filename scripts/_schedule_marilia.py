import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5511981674869"

async def main():
    from app.database import get_supabase
    from app.google_calendar import create_event
    from app.graph.tools import _get_doctor_calendar_id
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    patient_id = "00a6a8be-7f2e-4a8a-9514-0e107a8ec6aa"
    start = datetime(2026, 7, 13, 16, 30, 0, tzinfo=TZ)
    end = start + timedelta(hours=1)

    calendar_id = await _get_doctor_calendar_id("bruna")
    print(f"Criando evento em {calendar_id} para {start}")

    event_id = await create_event(
        calendar_id=calendar_id,
        start=start,
        slot_minutes=60,
        patient_name="Marília Felipe de Oliveira Tomal",
        doctor_name="Dra. Bruna",
        modality="online",
        patient_email="maoliveiratomal@gmail.com",
        patient_number=PHONE,
    )
    print(f"Evento criado: {event_id}")

    appt = await client.from_("appointments").insert({
        "appointment_id": event_id,
        "patient_id": patient_id,
        "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "modality": "online",
        "consultation_type": "primeira_consulta",
        "booking_fee_waived": True,
        "created_at": datetime.now(TZ).isoformat(),
        "updated_at": datetime.now(TZ).isoformat(),
    }).execute()
    print("Agendamento salvo no banco:", appt.data)

asyncio.run(main())
