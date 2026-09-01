import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for phone in ["558196962165", "5581996962165", "8196962165"]:
        msgs = await client.table("messages").select("created_at").eq("phone", phone).execute()
        print(f"phone={phone}: {len(msgs.data)} msgs")
        if msgs.data:
            dates = sorted(m["created_at"] for m in msgs.data)
            print("   first:", dates[0], "last:", dates[-1])

asyncio.run(main())
