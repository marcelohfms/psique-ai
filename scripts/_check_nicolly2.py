import asyncio
from datetime import datetime, timezone
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
    pid="93a1bf04-c48d-4083-87e1-b24d3cbe3233"
    # raw created_at
    r = await c.from_("appointments").select("appointment_id,status,start_time,created_at,updated_at,booking_fee_paid_at,booking_fee_waived,pending_reschedule,reschedule_initiated_by").eq("patient_id",pid).order("start_time").execute()
    print("=== Consultas Nicolly (raw created_at) ===")
    for a in r.data or []:
        print(f"{a['status']:>10} start={fmt(a['start_time'])} appt={a['appointment_id']}")
        print(f"           created_raw={a['created_at']} updated_raw={a['updated_at']}")
        print(f"           taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')} pending_resched={a.get('pending_reschedule')} by={a.get('reschedule_initiated_by')}")
    # events schema
    ev = await c.from_("events").select("*").limit(1).execute()
    print("\nEVENTS COLUMNS:", list(ev.data[0].keys()) if ev.data else "none")

asyncio.run(main())
