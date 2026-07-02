import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import get_available_slots as _get_slots, _parse_day
    from app.graph.tools import _get_doctor_calendar_id

    calendar_id = await _get_doctor_calendar_id("julio")
    base = _parse_day("quinta de agosto")
    print("Data base:", base)

    from datetime import timedelta
    for week in range(4):
        d = base + timedelta(weeks=week)
        slots = await _get_slots(calendar_id=calendar_id, preferred_day=d.isoformat(), preferred_shift="tarde", slot_minutes=60, doctor_key="julio")
        print(d, "->", [s[0].strftime("%H:%M") for s in slots])

asyncio.run(main())
