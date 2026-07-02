import asyncio, re
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    msgs = await client.from_("messages").select("content, created_at") \
        .eq("phone", "558195302944").eq("role", "user") \
        .ilike("content", "%drive_link%").execute()
    for m in msgs.data:
        print(m["content"])
        links = re.findall(r'drive_link:(https?://[^\]]+)', m["content"])
        print("Drive links:", links)

asyncio.run(main())
