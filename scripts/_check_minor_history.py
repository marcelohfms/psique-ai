import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.from_("messages").select("phone,created_at,content").ilike("content", "%duas partes de 1 hora%").order("created_at", desc=True).limit(10).execute()
    print(f"found {len(res.data)} matches for the split-explanation text")
    for r in res.data:
        print(r["created_at"], r["phone"], r["content"][:120])

asyncio.run(main())
