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
    ph="5581986651067"
    ev = await c.from_("events").select("event_type,created_at,metadata").eq("phone",ph).in_("event_type",["appointment_canceled","appointment_booked","payment_reminder_sent","booking_fee_reminder_sent"]).order("created_at").execute()
    for e in ev.data or []:
        print(f"\n──── {fmt(e['created_at'])} {e['event_type']}")
        print(json.dumps(e.get("metadata"), ensure_ascii=False, indent=2))
    # was a payment reminder ever sent (cron path)?
    print("\n=== reminders/paid_at na consulta cancelada 4u2jqniubmju802j3mgvnmc2b4 ===")
    a = await c.from_("appointments").select("*").eq("appointment_id","4u2jqniubmju802j3mgvnmc2b4").maybe_single().execute()
    d=a.data or {}
    for k in ("created_at","updated_at","booking_fee_paid_at","payment_reminder_sent_at","paid_at","status","booking_fee_waived"):
        print(f"  {k}: {d.get(k)}")

asyncio.run(main())
