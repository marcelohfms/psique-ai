import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    valdemar = "4095a643-dfc8-47bc-a348-86b8b4d2a242"
    fabricia = "e1c68d92-4b3b-4df3-92bd-bb821e827685"
    for pid, name in [(valdemar, "Valdemar"), (fabricia, "Fabrícia")]:
        pc = await client.from_("patient_contacts").select("*, contacts(phone, name)").eq("patient_id", pid).execute()
        print(f"\n=== patient_contacts de {name} ({pid}): {len(pc.data)} ===")
        for row in pc.data:
            print(f"  role={row.get('role')} | is_self={row.get('is_self')} | contact_id={row.get('contact_id')} | relationship={row.get('relationship')} | contact={row.get('contacts')}")

asyncio.run(main())
