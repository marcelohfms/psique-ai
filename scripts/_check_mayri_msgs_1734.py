import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581988851971"


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    resp = await (
        client.from_("messages")
        .select("*")
        .eq("phone", PHONE)
        .gte("created_at", "2026-07-07T00:00:00+00:00")
        .order("created_at")
        .execute()
    )
    print(f"mensagens de hoje (2026-07-07): {len(resp.data)}")
    for m in resp.data:
        print(f"  [{m['created_at']}] {m['role']}: {m['content'][:300]!r}")

asyncio.run(main())
