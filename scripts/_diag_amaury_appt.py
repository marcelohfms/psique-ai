import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()
    pat = await client.from_("patients").select("*").ilike("name", "%Amaury%").execute()
    for p in pat.data:
        print("PATIENT:", p)
        appts = await client.from_("appointments").select("*").eq("patient_id", p["id"]).order("start_time", desc=True).execute()
        for a in appts.data:
            print("  APPT:", a)

asyncio.run(main())
