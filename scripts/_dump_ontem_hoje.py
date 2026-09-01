import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    try: return datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except: return iso
PHONES = ["5581995138598","5581991542212","5581991827596","5581996503841"]
async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    for ph in PHONES:
        r = await c.from_("messages").select("role,content,created_at").eq("phone", ph).order("created_at").execute()
        print(f"\n===== {ph} =====")
        for m in (r.data or []):
            cont = (m.get("content") or "").replace("\n"," ")[:300]
            print(f"[{fmt(m['created_at'])}] {m['role']:9}: {cont}")
asyncio.run(main())
