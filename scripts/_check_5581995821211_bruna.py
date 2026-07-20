import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in ["5581995821211", "558195821211"]:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(80).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in reversed(msgs.data):
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:300]}")

asyncio.run(main())
