from dotenv import load_dotenv; load_dotenv()
import asyncio
from datetime import date, timedelta
from app.graph.tools import _get_doctor_calendar_id
from app.google_calendar import get_available_slots

async def main():
    cal = await _get_doctor_calendar_id("julio")
    print("calendar_id:", cal)
    start = date(2026, 7, 1)
    for i in range(14):  # 1..14 julho
        d = start + timedelta(days=i)
        iso = d.isoformat()
        all_slots = []
        for shift in ("manha", "tarde", "noite"):
            slots = await get_available_slots(cal, iso, shift, 60, "julio")
            for s, mod in slots:
                all_slots.append((s, mod))
        # dedupe
        seen = {}
        for s, mod in all_slots:
            seen[s] = mod
        wd = ["seg","ter","qua","qui","sex","sab","dom"][d.weekday()]
        if seen:
            times = ", ".join(f"{s.strftime('%H:%M')}({m})" for s, m in sorted(seen.items()))
            print(f"{iso} {wd}: {times}")
        else:
            print(f"{iso} {wd}: -")

asyncio.run(main())
