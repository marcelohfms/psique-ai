import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581998653907"
PHONE_ALT = "558198653907"  # sem o 9 adicional

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for phone in (PHONE, PHONE_ALT):
        contact = await client.table("contacts").select("*").eq("phone", phone).execute()
        print(f"CONTACT (phone={phone}):", contact.data)

        for c in contact.data:
            pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", c["id"]).execute()
            print("PATIENT_CONTACTS:")
            for row in pc.data:
                print(" role:", row.get("role"), "is_self:", row.get("is_self"), "relationship:", row.get("relationship"))
                print(" patient:", row.get("patients"))

        for c in contact.data:
            pc = await client.table("patient_contacts").select("patient_id").eq("contact_id", c["id"]).execute()
            patient_ids = {row["patient_id"] for row in pc.data}
            for pid in patient_ids:
                appts = await client.table("appointments").select("*").eq("patient_id", pid).execute()
                print(f"APPOINTMENTS (patient_id={pid}):", appts.data)

asyncio.run(main())
