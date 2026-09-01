import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(str(iso).replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except: return str(iso)

async def main():
    from app.supabase_client import get_supabase
    from app.graph.tools import DOCTOR_IDS
    inv={v:k for k,v in DOCTOR_IDS.items()}
    c = await get_supabase()
    lo = datetime(2026,8,28,0,0,tzinfo=TZ).astimezone(timezone.utc).isoformat()
    hi = datetime(2026,8,30,23,59,tzinfo=TZ).astimezone(timezone.utc).isoformat()
    r = await c.from_("appointments").select(
        "appointment_id,patient_id,doctor_id,start_time,status,booking_fee_paid_at,booking_fee_waived,confirmed_at,no_show_message_sent_at,pos_consulta_sent_at"
    ).gte("start_time",lo).lte("start_time",hi).order("start_time").execute()
    pids=list({a['patient_id'] for a in (r.data or []) if a.get('patient_id')})
    nm={}
    if pids:
        pr=await c.from_("patients").select("id,name").in_("id",pids).execute()
        nm={p['id']:p['name'] for p in pr.data or []}
    print("=== Consultas com data 28/08–30/08 ===")
    now=datetime.now(timezone.utc)
    for a in r.data or []:
        st=datetime.fromisoformat(a['start_time'].replace('Z','+00:00'))
        stuck = a['status']=='scheduled' and st<now
        print(f"{a['status']:>10} {fmt(a['start_time'])} {inv.get(a['doctor_id'],'?'):6} {nm.get(a.get('patient_id'),'?')[:24]:24} taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')} confirm={fmt(a.get('confirmed_at'))} {'⚠️PRESA(passou e scheduled)' if stuck else ''}")

asyncio.run(main())
