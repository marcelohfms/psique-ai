import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("appointments").select("*, patients(name)").eq("appointment_id", "l21846ehpa1ct30p34u37pnq0k").execute()
    for a in res.data:
        print("modality:", a.get("modality"), "| patient:", (a.get("patients") or {}).get("name"))

asyncio.run(main())
