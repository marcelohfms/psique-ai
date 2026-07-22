import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    r = await client.from_("messages").select("*").eq("phone", "5581996332827").eq("role", "assistant").order("created_at").execute()
    for m in r.data:
        if "taxa de reserva" in (m.get("content") or "").lower() and "R$ 100" in (m.get("content") or ""):
            print(m["created_at"])
            print(m["content"])
            print("=====")

asyncio.run(main())
