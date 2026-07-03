import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import get_available_slots as _get_slots, SCHEDULE_EXCEPTIONS
    from app.graph.tools import _get_doctor_calendar_id

    print("06/07 em SCHEDULE_EXCEPTIONS[julio]:", SCHEDULE_EXCEPTIONS.get("julio", {}).get("2026-07-06", "NÃO ENCONTRADO"))

    calendar_id = await _get_doctor_calendar_id("julio")
    for shift in ("manha", "tarde", "noite"):
        slots = await _get_slots(calendar_id=calendar_id, preferred_day="2026-07-06", preferred_shift=shift, slot_minutes=60, doctor_key="julio")
        print(shift, "->", [s[0].strftime("%H:%M") for s in slots])

asyncio.run(main())
