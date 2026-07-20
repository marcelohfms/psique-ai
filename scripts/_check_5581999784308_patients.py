import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581999784308"

async def main():
    from app.database import get_supabase, _phone_variants

    client = await get_supabase()

    for phone in _phone_variants(PHONE):
        c = await client.from_("contacts").select("*").eq("phone", phone).execute()
        if not c.data:
            continue
        print(f"=== CONTACT ({phone}) ===")
        for row in c.data:
            print(row)
            pc = await client.from_("patient_contacts").select("*, patients(*)").eq("contact_id", row["id"]).execute()
            print("=== PATIENT_CONTACTS ===")
            for link in pc.data:
                p = link.get("patients")
                print(f"  patient_id={link.get('patient_id')} relationship={link.get('relationship')} patient_name={p.get('name') if p else None} birth_date={p.get('birth_date') if p else None}")

asyncio.run(main())
