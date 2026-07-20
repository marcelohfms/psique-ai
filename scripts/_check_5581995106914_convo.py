import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in ["5581995106914", "558195106914", "5581995106914@s.whatsapp.net"]:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=False).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:300]}")

asyncio.run(main())
