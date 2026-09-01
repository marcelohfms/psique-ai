import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(str(iso).replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    except: return str(iso)

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    # events dos dois numeros desde 27/08 (remarcacao / pagamento)
    for ph in ["5581991749847","5581995397978"]:
        ev = await c.from_("events").select("event_type,created_at,metadata").eq("phone",ph).gte("created_at","2026-08-27").order("created_at").execute()
        print(f"\n=== eventos {ph} desde 27/08 ===")
        for e in ev.data or []:
            print(f"  {fmt(e['created_at'])} {e['event_type']}: {str(e.get('metadata'))[:200]}")
    # calendar event do 02/09
    try:
        from app.google_calendar import get_calendar_service
        from app.config import settings
        svc = get_calendar_service()
        for calname, calid in [("Julio", getattr(settings,'GOOGLE_CALENDAR_ID_JULIO', None) or getattr(settings,'CALENDAR_ID_JULIO', None))]:
            print(f"\n--- cal {calname} {calid} ---")
    except Exception as e:
        print("cal err", e)

asyncio.run(main())
