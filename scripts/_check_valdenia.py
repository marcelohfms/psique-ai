import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    users = await get_users_by_phone("5581981139373@s.whatsapp.net")
    print(f"Registros para 5581981139373:")
    for u in users:
        print(f"  id={u['id']} | name={u.get('name')} | patient_name={u.get('patient_name')} | is_patient={u.get('is_patient')} | doctor_id={u.get('doctor_id')}")

asyncio.run(main())
