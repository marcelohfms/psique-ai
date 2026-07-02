import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    contact = await client.table("contacts").select("*").eq("phone", "5581995217065").execute()
    print("CONTACT:", contact.data)

    for c in contact.data:
        pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", c["id"]).execute()
        print("PATIENT_CONTACTS:")
        for row in pc.data:
            print(" role:", row.get("role"), "is_self:", row.get("is_self"), "relationship:", row.get("relationship"))
            print(" patient:", row.get("patients"))

    appts = await client.table("appointments").select("*").eq("phone", "5581995217065").execute()
    print("APPOINTMENTS:", appts.data)

asyncio.run(main())
