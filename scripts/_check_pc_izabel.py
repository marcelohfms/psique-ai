import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    patient_id = "8b71f253-50d7-4f81-b227-d92233a3a359"
    pc = await client.from_("patient_contacts").select("*, contacts(*)").eq("patient_id", patient_id).execute()
    for row in pc.data:
        c = row.get("contacts") or {}
        print(f"role={row.get('role')} contact_id={row.get('contact_id')} phone={c.get('phone')} name={c.get('name')} active={c.get('active')} deactivated_at={c.get('deactivated_at')}")

asyncio.run(main())
