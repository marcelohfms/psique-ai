import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    msgs = await client.table("messages").select("*").eq("phone", "558196962165").order("created_at", desc=True).limit(200).execute()
    rows = [m for m in msgs.data if "2026-08-1" in (m.get("created_at") or "") or "2026-08-2" in (m.get("created_at") or "")]
    for m in reversed(rows):
        print(f"[{m.get('created_at')}] {m.get('role')}: {(m.get('content') or '')[:400]}")

asyncio.run(main())
