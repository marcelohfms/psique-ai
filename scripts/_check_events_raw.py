import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    import json
    client = await get_supabase()
    r = await client.from_("events").select("*").eq("phone", "5581996332827").order("created_at").execute()
    for row in r.data:
        print(json.dumps(row, indent=2, default=str))
        print("---")

asyncio.run(main())
