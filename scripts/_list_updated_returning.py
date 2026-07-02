import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    r = await client.from_("appointments").select("patient_id").eq("status", "completed").execute()
    patient_ids = sorted({a["patient_id"] for a in r.data if a.get("patient_id")})
    p = await client.from_("patients").select("id, name").in_("id", patient_ids).execute()

asyncio.run(main())
