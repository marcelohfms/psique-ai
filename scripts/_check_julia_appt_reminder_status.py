import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    appt = await client.from_("appointments").select("*").eq(
        "patient_id", "8b71f253-50d7-4f81-b227-d92233a3a359"
    ).order("start_time", desc=True).limit(5).execute()
    for a in appt.data:
        print({k: a.get(k) for k in (
            "appointment_id", "start_time", "status", "created_at",
            "reminder_day_before_sent_at", "reminder_day_of_sent_at",
        )})
    print("now (Recife):", datetime.now(TZ).isoformat())

asyncio.run(main())
