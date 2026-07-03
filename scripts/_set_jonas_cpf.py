import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    CPF = "080.253.214-44"

    r1 = await client.table("patients").update({"patient_cpf": CPF}).eq("id", "2ca01880-0c27-4305-bfb8-342c90065e08").execute()
    print("PATIENT UPDATED:", r1.data)

    r2 = await client.table("contacts").update({"cpf": CPF}).eq("id", "e29d64a7-0969-4058-9623-474508e72b45").execute()
    print("CONTACT UPDATED:", r2.data)

asyncio.run(main())
