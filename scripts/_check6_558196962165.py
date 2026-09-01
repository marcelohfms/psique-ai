import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    msgs = await client.table("messages").select("*").eq("phone", "5581996962165").gte("created_at","2026-08-13").lte("created_at","2026-08-18").order("created_at").execute()
    for m in msgs.data:
        print(f"[{m.get('created_at')}] {m.get('role')}: {(m.get('content') or '')[:400]}")

asyncio.run(main())
