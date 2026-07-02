import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_users_by_phone
    users = await get_users_by_phone("5581996001122@s.whatsapp.net")
    for u in users:
        print(f"id={u['id']} | name='{u.get('name')}' | patient_name='{u.get('patient_name')}' | is_patient={u.get('is_patient')}")

asyncio.run(main())
