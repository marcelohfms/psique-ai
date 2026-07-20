import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    appt = await client.from_("appointments").select("*").eq("id", "102294c0-e504-4e26-85bf-77a794c28733").execute()
    for row in appt.data:
        for k, v in row.items():
            print(f"  {k}: {v}")

asyncio.run(main())
