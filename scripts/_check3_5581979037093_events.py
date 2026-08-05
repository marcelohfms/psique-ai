import asyncio
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("America/Recife")
PHONE = "5581979037093"

async def main():
    from app.database import get_supabase
    c = await get_supabase()
    r = await c.from_("events").select("*").ilike("phone", f"%{PHONE}%").order("created_at").execute()
    print("events:", len(r.data))
    for e in r.data:
        ts = e.get("created_at")
        try: ts = datetime.fromisoformat(ts).astimezone(TZ).strftime("%d/%m %H:%M:%S.%f")[:-3]
        except Exception: pass
        print(f"[{ts}] {e['event_type']:32} {str(e.get('metadata'))[:260]}")

asyncio.run(main())
