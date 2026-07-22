import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    phone = "5581988054825"

    # events around deactivation time
    try:
        ev = await client.from_("events").select("*").eq("phone", phone).order("created_at", desc=True).limit(20).execute()
        print("=== events ===")
        for e in ev.data or []:
            print(e)
    except Exception as e:
        print("events query failed:", e)

    print("\n=== messages around 2026-07-17 ===")
    msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(30).execute()
    for m in msgs.data or []:
        ts = m.get("created_at")
        print(f"{ts} | {m.get('role')}: {str(m.get('content'))[:200]}")

asyncio.run(main())
