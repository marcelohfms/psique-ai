import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    contact = await client.table("contacts").select("*").eq("phone", "5581999655881").execute()
    print("CONTACT:", contact.data)

    for c in contact.data:
        pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", c["id"]).execute()
        print("PATIENT_CONTACTS:", pc.data)

asyncio.run(main())
