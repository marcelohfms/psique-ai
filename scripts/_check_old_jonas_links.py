import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    OLD_ID = "2ca01880-0c27-4305-bfb8-342c90065e08"
    pc = await client.table("patient_contacts").select("*, contacts(*)").eq("patient_id", OLD_ID).execute()
    print("OLD PATIENT CONTACTS:")
    for row in pc.data:
        print(" role:", row.get("role"), "is_self:", row.get("is_self"), "rel:", row.get("relationship"), "contact:", row.get("contacts"))

    appts = await client.table("appointments").select("*").eq("patient_id", OLD_ID).execute()
    print("OLD APPOINTMENTS:", appts.data)

asyncio.run(main())
