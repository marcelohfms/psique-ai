import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except: return iso

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    pid="93a1bf04-c48d-4083-87e1-b24d3cbe3233"
    r = await c.from_("appointments").select("*").eq("patient_id",pid).order("start_time").execute()
    print("=== TODAS as consultas da Nicolly ===")
    for a in r.data or []:
        print(f"{a['status']:>10} {fmt(a['start_time'])} taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')} paid_at={fmt(a.get('paid_at'))} appt={a['appointment_id']} criado={fmt(a['created_at'])}")
    p = await c.from_("patients").select("*").eq("id",pid).maybe_single().execute()
    pd=p.data or {}
    print("\n=== Paciente ===")
    for k in ("name","status","custom_price","booking_fee_waived","date_of_birth","is_returning_patient","preferred_doctor"):
        print(f"  {k}: {pd.get(k)}")
    # contact
    cid = pd.get("id")
    ct = await c.from_("contacts").select("phone,active,name").eq("patient_id",cid).execute() if False else None
    # events
    ev = await c.from_("events").select("event_type,created_at,metadata").eq("patient_id",pid).order("created_at",desc=True).limit(15).execute()
    print("\n=== Eventos recentes ===")
    for e in ev.data or []:
        print(f"  {fmt(e['created_at'])} {e['event_type']} {str(e.get('metadata'))[:80]}")

asyncio.run(main())
