import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    result = await client.from_("users").select("id, number, name, patient_name, doctor_id, is_patient, active").ilike("name", "%helder%").execute()
    result2 = await client.from_("users").select("id, number, name, patient_name, doctor_id, is_patient, active").ilike("patient_name", "%helder%").execute()
    rows = {r["id"]: r for r in result.data + result2.data}.values()
    for r in rows:
        print(r)

asyncio.run(main())
