import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    calendar_id = await _get_doctor_calendar_id("julio")
    # Quintas de julho: 02, 09, 16, 23, 30
    quintas = [("02/07"), ("09/07"), ("16/07"), ("23/07"), ("30/07")]
    for day in quintas:
        slots = await get_available_slots(calendar_id, day, "tarde", 60, "julio")
        if slots:
            horarios = ", ".join(s.astimezone(TZ).strftime("%H:%M") for s, _ in slots)
            print(f"Quinta {day}: {horarios}")
        else:
            print(f"Quinta {day}: sem vagas")

asyncio.run(main())
