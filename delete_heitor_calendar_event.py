import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def delete_heitor_event():
    from app.google_calendar import cancel_event
    from app.database import _get_doctor_calendar_id

    # Heitor's appointment is with Dr. Júlio
    calendar_id = await _get_doctor_calendar_id("julio")

    # The event ID is the appointment_id
    event_id = "opsndh1sv1pkgbic4tmul1ihm8"

    print(f"Deletando evento do Google Calendar:")
    print(f"  Calendar ID: {calendar_id}")
    print(f"  Event ID: {event_id}")
    print()

    try:
        await cancel_event(calendar_id, event_id)
        print("✅ Evento deletado com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao deletar: {e}")

asyncio.run(delete_heitor_event())
