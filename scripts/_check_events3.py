import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    events = await client.from_("events").select("*").eq("phone", "5581996332827").order("created_at", desc=False).execute()
    print(f"{len(events.data)} eventos:")
    for e in events.data:
        dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
        print(f"  {dt} | {e['event_type']} | {e.get('data')}")

asyncio.run(main())
