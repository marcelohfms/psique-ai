import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in ["5581995186399", "558195186399", "5581995186399@s.whatsapp.net"]:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=False).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:400]}")

asyncio.run(main())
