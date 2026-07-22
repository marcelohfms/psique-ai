import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()
    patient_id = "1959c0e3-0da1-45c9-93c4-3bf9e029cd9b"
    cpf = "13685085450"

    r = await client.from_("patients").update({"patient_cpf": cpf}).eq("id", patient_id).execute()
    print("Atualizado:", r.data)

asyncio.run(main())
