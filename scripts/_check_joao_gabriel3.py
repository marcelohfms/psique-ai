import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    r = await client.from_("users").select("*").eq("id", "95586b6b-d08e-4e5e-be0e-813f937b661f").single().execute()
    u = r.data
    fields = ["name","patient_name","birth_date","age","patient_cpf","email","guardian_name","guardian_cpf","guardian_relationship","is_patient","is_returning_patient","doctor_id"]
    for f in fields:
        print(f"  {f}: {u.get(f)}")

asyncio.run(main())
