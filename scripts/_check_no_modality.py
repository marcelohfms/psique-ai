import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime, timezone
    client = await get_supabase()

    now = datetime.now(timezone.utc).isoformat()
    res = await client.table("appointments").select("*, patients(name)").eq("status", "scheduled").is_("modality", "null").gte("start_time", now).execute()
    for a in res.data:
        from zoneinfo import ZoneInfo
        TZ = ZoneInfo("America/Recife")
        dt = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        name = (a.get("patients") or {}).get("name", "?")
        print(f"{dt} | {name} | appt={a['appointment_id']}")

asyncio.run(main())
