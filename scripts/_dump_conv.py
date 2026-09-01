import asyncio, sys
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    phone = sys.argv[1]
    since = sys.argv[2] if len(sys.argv) > 2 else "2026-08-31T00:00:00+00:00"
    msgs = (await client.table("messages").select("*")
            .eq("phone", phone).gte("created_at", since)
            .order("created_at").execute()).data
    for m in msgs:
        print(f"[{m['created_at']}] {m['role']}: {str(m.get('content'))[:500]}")

asyncio.run(main())
