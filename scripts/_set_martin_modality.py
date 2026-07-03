import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("appointments").update({"modality": "presencial"}).eq("appointment_id", "oktcvjec759pjnu9vl2bfqje2g").execute()
    print("UPDATED:", res.data[0]["modality"], res.data[0]["start_time"])

asyncio.run(main())
