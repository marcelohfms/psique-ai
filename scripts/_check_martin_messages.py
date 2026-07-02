import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    msgs = await client.table("messages").select("*").eq("phone", "5581996980383").order("created_at", desc=False).execute()
    for m in msgs.data[-40:]:
        print(m.get("created_at"), "|", m.get("role"), "|", (m.get("content") or "")[:200])

asyncio.run(main())
