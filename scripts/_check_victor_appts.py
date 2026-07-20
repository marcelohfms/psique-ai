import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    patients = await client.from_("patients").select("*").ilike("name", "%Victor%Fran%").execute()
    print(f"patients found: {len(patients.data)}")
    for p in patients.data:
        print(p)
        appts = await client.from_("appointments").select("*").eq("patient_id", p["id"]).order("created_at").execute()
        print(f"  appointments: {len(appts.data)}")
        for a in appts.data:
            print("   ", a)

asyncio.run(main())
