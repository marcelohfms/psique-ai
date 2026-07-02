import asyncio
from datetime import date, timedelta

BRUNA_CAL = "brunalima.psiquiatra@gmail.com"

async def main():
    from app.google_calendar import get_available_slots, SCHEDULE_EXCEPTIONS
    
    today = date(2026, 6, 15)
    
    for days_ahead in range(1, 32):
        d = today + timedelta(days=days_ahead)
        weekday = d.weekday()
        if weekday not in (0, 2, 4):  # Mon, Wed, Fri
            continue
        date_str = d.strftime("%Y-%m-%d")
        # Check if blocked by exception
        exc = SCHEDULE_EXCEPTIONS.get("bruna", {})
        if date_str in exc and exc[date_str] == []:
            continue
        slots = await get_available_slots(BRUNA_CAL, date_str, preferred_shift="qualquer", doctor_key="bruna")
        if slots:
            day_name = d.strftime("%d/%m/%Y (%A)")
            for s, mod in slots:
                print(f"{day_name} {s.strftime('%H:%M')} ({mod})")

asyncio.run(main())
