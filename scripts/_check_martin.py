import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("patients").select("*").ilike("name", "%Martin%").execute()
    print("PATIENTS:", res.data)
    for p in res.data:
        appts = await client.table("appointments").select("*").eq("patient_id", p["id"]).execute()
        print(f"--- Appointments for {p['name']} ({p['id']}):", appts.data)
        pc = await client.table("patient_contacts").select("*, contacts(*)").eq("patient_id", p["id"]).execute()
        print(f"--- Contacts:", [(r.get("role"), r.get("contacts",{}).get("phone")) for r in pc.data])

asyncio.run(main())
