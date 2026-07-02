import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    calendar_id = await _get_doctor_calendar_id("julio")
    days = [("segunda", "22/06"), ("terça", "23/06"), ("quarta", "24/06"), ("quinta", "25/06"), ("sexta", "26/06")]

    for label, day in days:
        slots = await get_available_slots(calendar_id, day, "qualquer", 60, "julio")
        if slots:
            horarios = ", ".join(s.astimezone(TZ).strftime("%H:%M") for s, _ in slots)
            print(f"{label.upper()} {day}: {horarios}")
        else:
            print(f"{label.upper()} {day}: sem vagas")

asyncio.run(main())
