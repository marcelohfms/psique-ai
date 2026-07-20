import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    r = await client.from_("appointments").select("*").eq("appointment_id", "qge4kap72m8lu2msq7asdoea2g").maybe_single().execute()
    for k, v in (r.data or {}).items():
        print(f"  {k}: {v}")

asyncio.run(main())
