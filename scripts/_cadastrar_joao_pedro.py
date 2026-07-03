import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_ID = "0dc2d864-1b36-410f-a92e-4dcf9e16a53c"
PHONE = "5581992424522"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    contact = await client.from_("contacts").insert({
        "phone": PHONE,
        "name": "Reinaldo Gomes Filho",
        "cpf": "962.730.034-91",
    }).execute()
    contact_id = contact.data[0]["id"]
    print("Contato criado:", contact.data[0])

    for role in ("agendamento", "consulta", "financeiro"):
        link = await client.from_("patient_contacts").insert({
            "patient_id": PATIENT_ID,
            "contact_id": contact_id,
            "role": role,
            "is_self": False,
            "relationship": "pai",
        }).execute()
    print("3 vínculos patient_contacts criados (is_self=False, relationship=pai)")

asyncio.run(main())
