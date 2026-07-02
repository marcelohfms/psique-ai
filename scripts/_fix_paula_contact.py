import asyncio
from dotenv import load_dotenv
load_dotenv()

CONTACT_ID = "1f7c6e11-138c-434d-8e1f-5b82c0f2b1d3"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Corrige o nome do contato para Glória (mãe), não Paula (paciente)
    await client.from_("contacts").update({"name": "Glória Muniz"}).eq("id", CONTACT_ID).execute()
    print("Nome do contato atualizado para Glória Muniz")

    # Corrige os vínculos patient_contacts: não é self, é mãe
    r = await client.from_("patient_contacts").update({
        "is_self": False,
        "relationship": "mãe",
    }).eq("contact_id", CONTACT_ID).execute()
    print(f"{len(r.data)} vínculos patient_contacts atualizados (is_self=False, relationship=mãe)")

asyncio.run(main())
