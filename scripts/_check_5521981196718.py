import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONES = ["5521981196718", "21981196718", "552181196718"]

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    patient_ids = set()

    for phone in PHONES:
        contact = await client.table("contacts").select("*").eq("phone", phone).execute()
        if contact.data:
            print(f"=== CONTACT match on {phone} ===")
            for c in contact.data:
                print(" contact_id:", c.get("id"), "name:", c.get("name"), "active:", c.get("active"))
                pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", c["id"]).execute()
                for row in pc.data:
                    p = row.get("patients") or {}
                    patient_ids.add(p.get("id"))
                    print("  role:", row.get("role"), "is_self:", row.get("is_self"), "relationship:", row.get("relationship"))
                    print("    patient:", p.get("id"), p.get("name"), "dob:", p.get("date_of_birth"), "email:", p.get("email"))

    print("\n=== APPOINTMENTS for patient_ids:", patient_ids, "===")
    for pid in patient_ids:
        if not pid:
            continue
        appts = await client.table("appointments").select("*").eq("patient_id", pid).order("start_time").execute()
        for a in appts.data:
            print(" appt:", a.get("appointment_id"), "patient_id:", a.get("patient_id"))
            print("   start:", a.get("start_time"), "end:", a.get("end_time"), "status:", a.get("status"), "modality:", a.get("modality"))
            print("   booking_fee_paid_at:", a.get("booking_fee_paid_at"), "booking_fee_waived:", a.get("booking_fee_waived"), "custom_price:", a.get("custom_price"))
            print("   calendar_event_id:", a.get("calendar_event_id"), "doctor:", a.get("doctor"))

    print("\n=== LAST MESSAGES ===")
    for phone in PHONES:
        msgs = await client.table("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(50).execute()
        if msgs.data:
            print(f"-- on {phone} --")
            for m in reversed(msgs.data):
                print(f" [{m.get('created_at')}] {m.get('role')}: {str(m.get('content'))[:400]}")

asyncio.run(main())
