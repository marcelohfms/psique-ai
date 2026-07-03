import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for term in ["rebeka", "felipe"]:
        r1 = await client.from_("users").select("id, number, name, patient_name, is_patient, doctor_id").ilike("name", f"%{term}%").execute()
        r2 = await client.from_("users").select("id, number, name, patient_name, is_patient, doctor_id").ilike("patient_name", f"%{term}%").execute()
        rows = {r["id"]: r for r in r1.data + r2.data}.values()
        for r in rows:
            print(r)

asyncio.run(main())
