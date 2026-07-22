import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581988851971"


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # 1. Re-check messages table for anything after 21:12 UTC on 07/07
    resp = await (
        client.from_("messages")
        .select("*")
        .eq("phone", PHONE)
        .gte("created_at", "2026-07-07T21:00:00+00:00")
        .order("created_at")
        .execute()
    )
    print(f"=== mensagens após 21:00 UTC 07/07: {len(resp.data)} ===")
    for m in resp.data:
        print(f"  [{m['created_at']}] {m['role']}: {m['content'][:300]!r}")

    # 2. Check events table for payment_receipt_registered
    ev = await (
        client.from_("events")
        .select("*")
        .eq("phone", PHONE)
        .order("created_at", desc=True)
        .limit(15)
        .execute()
    )
    print(f"\n=== eventos recentes: {len(ev.data)} ===")
    for e in ev.data:
        print(f"  [{e['created_at']}] {e['event_type']}: {e.get('metadata')}")

asyncio.run(main())
