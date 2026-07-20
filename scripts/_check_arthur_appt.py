import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()
    res = await client.from_("appointments").select("*").eq("patient_id", 
        (await client.from_("patients").select("id").ilike("name", "%Arthur%Clark%").execute()).data[0]["id"]
    ).execute()
    for r in res.data:
        print(r)

asyncio.run(main())
