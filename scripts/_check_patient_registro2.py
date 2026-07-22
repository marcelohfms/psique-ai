import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()
    patient_id = "1959c0e3-0da1-45c9-93c4-3bf9e029cd9b"

    print("=== patients ===")
    p = await client.from_("patients").select("*").eq("id", patient_id).execute()
    for row in p.data:
        for k, v in row.items():
            print(f"  {k}: {v}")

    print("\n=== patient_contacts (todos vínculos) ===")
    pc = await client.from_("patient_contacts").select("*, contacts(*)").eq("patient_id", patient_id).execute()
    for row in pc.data:
        print(row)

asyncio.run(main())
