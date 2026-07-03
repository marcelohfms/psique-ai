import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    users = await get_users_by_phone("5587981054742@s.whatsapp.net")
    for u in users:
        print(u)

asyncio.run(main())
