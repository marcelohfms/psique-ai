import asyncio, json
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    msgs = await client.from_("messages").select("*").eq("phone", "5581995821211").order("created_at", desc=False).execute()
    for m in msgs.data:
        role = m.get("role")
        if role not in ("user", "assistant"):
            print(role, json.dumps(m, default=str)[:500])
    print("total:", len(msgs.data))
    print("roles:", set(m.get("role") for m in msgs.data))

asyncio.run(main())
