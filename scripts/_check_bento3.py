import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(str(iso).replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M:%S")
    except: return str(iso)

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    for ph in ["5581991749847","5581995397978"]:
        ev = await c.from_("events").select("event_type,created_at,metadata").eq("phone",ph).gte("created_at","2026-08-26").order("created_at").execute()
        print(f"\n=== eventos {ph} desde 26/08 ===")
        for e in ev.data or []:
            print(f"  {fmt(e['created_at'])} {e['event_type']}: {str(e.get('metadata'))[:120]}")
        msgs = await c.from_("messages").select("role,content,created_at").eq("phone",ph).gte("created_at","2026-08-28").order("created_at").execute()
        print(f"  --- mensagens {ph} desde 28/08 ---")
        for m in msgs.data or []:
            print(f"   {fmt(m['created_at'])} [{m['role']}] {str(m.get('content'))[:110]}")

asyncio.run(main())
