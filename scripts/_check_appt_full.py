import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    r = await client.from_("appointments").select("*").eq("appointment_id", "bb3psfo966q5vqhq0kc4bifpms").maybe_single().execute()
    import json
    print(json.dumps(r.data, indent=2, default=str))

asyncio.run(main())
