import asyncio
from dotenv import load_dotenv
load_dotenv()

from app.database import _strip_phone

RAW = "558796373892"
PHONES = list(dict.fromkeys([
    RAW,
    _strip_phone(RAW),
    "5587996373892",   # com 9º dígito
    "87996373892",
    "8796373892",
]))

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    print("variants:", PHONES)

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
                    print("    patient:", p.get("id"), p.get("name"), "dob:", p.get("date_of_birth"))

    print("\n=== APPOINTMENTS ===")
    for pid in patient_ids:
        if not pid:
            continue
        appts = await client.table("appointments").select("*").eq("patient_id", pid).order("start_time").execute()
        for a in appts.data:
            print(" appt:", a.get("appointment_id"), "start:", a.get("start_time"), "end:", a.get("end_time"),
                  "status:", a.get("status"), "doctor:", a.get("doctor"), "modality:", a.get("modality"))
            print("   booking_fee_paid_at:", a.get("booking_fee_paid_at"), "waived:", a.get("booking_fee_waived"),
                  "cal:", a.get("calendar_event_id"))

    print("\n=== MESSAGES ===")
    for phone in PHONES:
        msgs = await client.table("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(60).execute()
        if msgs.data:
            print(f"-- on {phone} ({len(msgs.data)} msgs) --")
            for m in reversed(msgs.data):
                print(f" [{m.get('created_at')}] {m.get('role')}: {str(m.get('content'))[:500]}")

asyncio.run(main())
