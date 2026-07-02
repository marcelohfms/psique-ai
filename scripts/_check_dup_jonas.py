import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("patients").select("*").ilike("name", "%Jonas Santos Ferreira%").execute()
    print("BY NAME:", res.data)
    res2 = await client.table("patients").select("*").eq("birth_date", "09/07/1987").execute()
    print("BY BIRTHDATE:", res2.data)

asyncio.run(main())
