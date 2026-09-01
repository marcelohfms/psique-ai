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
    from app.graph.tools import DOCTOR_IDS
    inv={v:k for k,v in DOCTOR_IDS.items()}
    c = await get_supabase()
    contact_id="2e409f80-2937-4bee-8a26-ebdb1f29e6b7"  # Daniella
    pc = await c.from_("patient_contacts").select("patient_id,is_self,relationship").eq("contact_id",contact_id).execute()
    print("=== Pacientes ligados ao contato da Daniella ===")
    pcols = (await c.from_("patients").select("*").limit(1).execute())
    print("patients cols:", list(pcols.data[0].keys()) if pcols.data else "?")
    for x in pc.data or []:
        p = await c.from_("patients").select("*").eq("id",x["patient_id"]).maybe_single().execute()
        pd=p.data or {}
        print(f"\n patient={pd.get('name')} id={x['patient_id']} is_self={x['is_self']} rel={x['relationship']} returning={pd.get('is_returning_patient')} waived={pd.get('booking_fee_waived')} status={pd.get('status')}")
        ap = await c.from_("appointments").select("appointment_id,doctor_id,start_time,status,created_at,updated_at,booking_fee_paid_at,booking_fee_waived,paid_at,confirmed_at,consultation_type,pending_reschedule,no_show_message_sent_at").eq("patient_id",x["patient_id"]).order("start_time").execute()
        for a in ap.data or []:
            print(f"   [{a['status']:>10}] {fmt(a['start_time'])} {inv.get(a['doctor_id'],'?'):6} taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')} confirm={fmt(a.get('confirmed_at'))} tipo={a.get('consultation_type')} appt={a['appointment_id']}")
            print(f"                 criado={fmt(a['created_at'])} atualizado={fmt(a['updated_at'])} pending_resched={a.get('pending_reschedule')} noshow={fmt(a.get('no_show_message_sent_at'))}")

asyncio.run(main())
