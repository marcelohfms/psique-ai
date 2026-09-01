import asyncio, json
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
    ph="5581991749847"
    # full metadata of both payment_receipt_registered events + any document/media events
    ev = await c.from_("events").select("event_type,created_at,metadata").eq("phone",ph).gte("created_at","2026-08-28").lte("created_at","2026-08-29T23:59").order("created_at").execute()
    print("=== TODOS eventos 28-29/08 (metadata completa) ===")
    for e in ev.data or []:
        print(f"\n── {fmt(e['created_at'])} {e['event_type']}")
        print("   "+json.dumps(e.get("metadata"), ensure_ascii=False))
    # documents table for Bento
    print("\n=== documents (Bento/Daniella) ===")
    for tbl_try in ["documents"]:
        try:
            d = await c.from_(tbl_try).select("*").eq("phone",ph).execute()
            for x in d.data or []:
                print("  ", json.dumps(x, ensure_ascii=False, default=str)[:300])
        except Exception as ex:
            print("  (no phone col)", ex)

asyncio.run(main())
