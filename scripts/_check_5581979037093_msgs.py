import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("America/Recife")
PHONE = "5581979037093"

async def main():
    from app.database import get_supabase
    c = await get_supabase()
    r = await c.from_("messages").select("*").ilike("phone", f"%{PHONE}%").order("created_at").execute()
    print("rows:", len(r.data))
    for m in r.data:
        ts = m.get("created_at")
        try: ts = datetime.fromisoformat(ts).astimezone(TZ).strftime("%d/%m %H:%M:%S")
        except Exception: pass
        keys = {k: v for k, v in m.items() if k not in ("content", "created_at")}
        print(f"\n[{ts}] {keys}")
        print("  ", str(m.get("content"))[:600].replace("\n", " | "))

asyncio.run(main())
