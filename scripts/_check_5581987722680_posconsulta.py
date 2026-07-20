import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581987722680"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    contacts = await client.from_("contacts").select("*").ilike("phone", f"%{PHONE[-10:]}%").execute()
    print(f"contacts found: {len(contacts.data)}")
    for c in contacts.data:
        print(" contact:", c)

    pc = await client.from_("patient_contacts").select("*, patients(*)").in_(
        "contact_id", [c["id"] for c in contacts.data]
    ).execute() if contacts.data else None
    patient_ids = set()
    if pc:
        print(f"patient_contacts found: {len(pc.data)}")
        for row in pc.data:
            print(" link:", row.get("contact_id"), row.get("patient_id"), row.get("role"))
            patient_ids.add(row.get("patient_id"))

    for pid in patient_ids:
        appts = await client.from_("appointments").select("*").eq("patient_id", pid).order("start_time", desc=True).limit(10).execute()
        print(f"appointments for patient_id={pid}: {len(appts.data)}")
        for a in appts.data:
            print("  ", {
                "appointment_id": a.get("appointment_id"),
                "start_time": a.get("start_time"),
                "end_time": a.get("end_time"),
                "status": a.get("status"),
                "confirmed_at": a.get("confirmed_at"),
                "pos_consulta_sent_at": a.get("pos_consulta_sent_at"),
            })

asyncio.run(main())
