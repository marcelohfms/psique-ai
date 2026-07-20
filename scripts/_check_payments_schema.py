import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("payments").select("*").eq("appointment_id", "fe933f0f-0409-4411-b472-b818c9f78195").execute()
    print("payments for this appointment:", res.data)

asyncio.run(main())
