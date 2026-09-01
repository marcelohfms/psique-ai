import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
PHONE = "5581996503841"
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    except: return iso
async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    ct = await c.from_("contacts").select("*").eq("phone", PHONE).execute()
    for contact in (ct.data or []):
        print("CONTACT:", contact.get("id"), contact.get("name"), contact.get("phone"))
        links = await c.from_("patient_contacts").select("*").eq("contact_id", contact["id"]).execute()
        for lk in (links.data or []):
            p = (await c.from_("patients").select("*").eq("id", lk["patient_id"]).execute()).data
            for pat in p:
                print("  PATIENT:", pat.get("id"), "|", pat.get("name"), "| nasc:", pat.get("birth_date"))
                appts = (await c.from_("appointments").select("appointment_id,doctor_id,start_time,status,paid_at,booking_fee_paid_at,booking_fee_waived,consultation_type").eq("patient_id", pat["id"]).order("start_time").execute()).data
                for a in (appts or []):
                    print(f"    APPT {a['appointment_id']} {fmt(a['start_time'])} [{a['status']}] paid_at={fmt(a.get('paid_at'))} taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')} tipo={a.get('consultation_type')}")
                pays = (await c.from_("payments").select("*").eq("patient_id", pat["id"]).order("created_at").execute()).data
                for pay in (pays or []):
                    print(f"    PAY  {fmt(pay.get('created_at'))} tipo={pay.get('payment_type')} valor={pay.get('amount')} appt={pay.get('appointment_id')} drive={pay.get('drive_link')}")
asyncio.run(main())
