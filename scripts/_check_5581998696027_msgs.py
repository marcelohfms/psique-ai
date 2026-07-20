import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, _phone_variants
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in _phone_variants("5581998696027"):
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(30).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in reversed(msgs.data):
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {(m.get('content') or '')[:300]}")

asyncio.run(main())
