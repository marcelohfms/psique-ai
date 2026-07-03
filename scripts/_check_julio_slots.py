import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    calendar_id = await _get_doctor_calendar_id("julio")

    days = [
        ("segunda", "15/06"),
        ("terça",   "16/06"),
        ("quarta",  "17/06"),
        ("quinta",  "18/06"),
        ("sexta",   "19/06"),
    ]

    for day_label, day_str in days:
        slots = await get_available_slots(
            calendar_id=calendar_id,
            preferred_day=day_str,
            preferred_shift="qualquer",
            slot_minutes=60,
            doctor_key="julio",
        )
        if slots:
            print(f"\n{day_label.upper()} {day_str}:")
            for start, modality in slots:
                print(f"  {start.astimezone(TZ).strftime('%H:%M')} [{modality or 'escolha'}]")
        else:
            print(f"\n{day_label.upper()} {day_str}: sem vagas")

asyncio.run(main())
