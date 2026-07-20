import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, _phone_variants, get_user_by_phone, DOCTOR_NAMES
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    user = await get_user_by_phone("5581985806544@s.whatsapp.net")
    print("=== USER (legacy view) ===")
    print(user)

    patient_id = (user or {}).get("patient_id")
    contact_id = (user or {}).get("contact_id") or (user or {}).get("id")

    if not patient_id:
        # try direct contact lookup across phone variants
        for phone in _phone_variants("5581985806544"):
            c = await client.from_("contacts").select("*").eq("phone", phone).execute()
            if c.data:
                print(f"=== CONTACT ({phone}) ===")
                print(c.data)
                for row in c.data:
                    pc = await client.from_("patient_contacts").select("*, patients(*)").eq("contact_id", row["id"]).execute()
                    print("=== PATIENT_CONTACTS ===")
                    print(pc.data)

    print("\n=== APPOINTMENTS by patient_id ===")
    if patient_id:
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, status, doctor_id, created_at, "
            "payment_reminder_sent_at, booking_fee_paid_at, booking_fee_waived, consultation_type, updated_at"
        ).eq("patient_id", patient_id).order("start_time", desc=True).execute()
        for a in appts.data:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            created = datetime.fromisoformat(a["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M") if a.get("created_at") else None
            updated = datetime.fromisoformat(a["updated_at"]).astimezone(TZ).strftime("%d/%m %H:%M") if a.get("updated_at") else None
            reminder = a.get("payment_reminder_sent_at")
            if reminder:
                reminder = datetime.fromisoformat(reminder).astimezone(TZ).strftime("%d/%m %H:%M")
            print(f"  id={a['appointment_id']} start={st} status={a['status']} doctor={DOCTOR_NAMES.get(a.get('doctor_id',''),'')} "
                  f"created={created} updated={updated} reminder_sent={reminder} fee_paid_at={a.get('booking_fee_paid_at')} "
                  f"fee_waived={a.get('booking_fee_waived')} type={a.get('consultation_type')}")

    print("\n=== EVENTS log ===")
    for phone in _phone_variants("5581985806544"):
        ev = await client.from_("events").select("*").eq("phone", phone).order("created_at", desc=True).limit(30).execute()
        for e in ev.data:
            dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {e.get('event_type')} | {e.get('metadata')}")

asyncio.run(main())
