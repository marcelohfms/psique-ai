import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()
    contact_id = "3eba48ce-797f-411d-bc38-75b7dc6bebce"
    cpf = "13685085450"

    r = await client.from_("contacts").update({"cpf": cpf}).eq("id", contact_id).execute()
    print("contacts atualizado:", r.data)

asyncio.run(main())
