import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    c = await client.from_("contacts").select("*").eq("phone", "5581999656460").execute()
    print("contact:", c.data)

    if c.data:
        cid = c.data[0]["id"]
        pc = await client.from_("patient_contacts").select("*").eq("contact_id", cid).execute()
        print("\npatient_contacts:", pc.data)
        for row in pc.data:
            p = await client.from_("patients").select("*").eq("id", row["patient_id"]).execute()
            print("\npatient:", p.data)

asyncio.run(main())
