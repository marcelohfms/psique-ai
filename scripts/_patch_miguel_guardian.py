import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()
    contact_id = "3eba48ce-797f-411d-bc38-75b7dc6bebce"
    patient_id = "1959c0e3-0da1-45c9-93c4-3bf9e029cd9b"

    r1 = await client.from_("contacts").update({"name": "Joaneide Siqueira De Oliveira Arruda"}).eq("id", contact_id).execute()
    print("contacts atualizado:", r1.data)

    r2 = await client.from_("patient_contacts").update({"is_self": False, "relationship": "mãe"}).eq("patient_id", patient_id).eq("contact_id", contact_id).execute()
    print("patient_contacts atualizado:", r2.data)

asyncio.run(main())
