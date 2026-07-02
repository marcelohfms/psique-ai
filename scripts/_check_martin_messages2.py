import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    msgs = await client.table("messages").select("*").eq("phone", "5581996980383").order("created_at", desc=False).execute()
    for m in msgs.data:
        ts = m.get("created_at")
        if ts and "2026-06-30T16:2" in ts:
            print(ts, "|", m.get("role"), "|", m.get("content"))

asyncio.run(main())
