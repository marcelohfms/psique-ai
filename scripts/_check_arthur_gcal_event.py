import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # doctors table
    docs = await client.from_("doctors").select("*").execute()
    for d in docs.data:
        print(d)

asyncio.run(main())
