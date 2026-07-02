import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("doctors").select("*").eq("doctor_id", "18b01f87-eacd-4905-bd4a-a8293991e6fd").execute()
    print(res.data)

asyncio.run(main())
