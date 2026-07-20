import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in ["5581981464986", "558181464986"]:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=False).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:500]}")

        events = await client.from_("events").select("*").eq("phone", phone).order("created_at", desc=False).execute()
        print(f"\n=== events phone={phone}: {len(events.data)} ===")
        for e in events.data:
            dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {e.get('event_type')} | {e.get('data')}")

asyncio.run(main())
