import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("patients").select("*").ilike("name", "%ANDREANA%").execute()
    print("patients ANDREANA:", res.data)
    res2 = await client.table("contacts").select("*").ilike("name", "%ANDREANA%").execute()
    print("contacts ANDREANA:", res2.data)

asyncio.run(main())
