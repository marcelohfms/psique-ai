import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()
    msgs = await client.from_("messages").select("*").eq("phone", "5581996332827").order("created_at", desc=True).limit(10).execute()
    for m in reversed(msgs.data):
        dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
        print(f"{dt} | {m.get('role')} | {m.get('content','')[:60]}")

asyncio.run(main())
