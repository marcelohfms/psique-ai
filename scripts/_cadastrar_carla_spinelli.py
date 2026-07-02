import asyncio
from dotenv import load_dotenv
load_dotenv()

CONTACT_ID = "f9ef9bdb-52be-4ac9-b578-0b4f526cba97"
BRUNA_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    patient = await client.from_("patients").insert({
        "name": "Carla Spinelli Ferrari Arruda",
        "birth_date": "06/03/1970",
        "age": 56,
        "patient_cpf": "793.425.824-00",
        "email": "carla.spinelli2011@gmail.com",
        "doctor_id": BRUNA_ID,
        "custom_price": 650,
        "is_returning_patient": False,
    }).execute()
    patient_id = patient.data[0]["id"]
    print("Paciente criada:", patient.data[0])

    # Atualiza CPF do contato (estava sem)
    await client.from_("contacts").update({"cpf": "793.425.824-00"}).eq("id", CONTACT_ID).execute()

    for role in ("agendamento", "consulta", "financeiro"):
        await client.from_("patient_contacts").insert({
            "patient_id": patient_id,
            "contact_id": CONTACT_ID,
            "role": role,
            "is_self": True,
            "relationship": "self",
        }).execute()
    print("3 vínculos patient_contacts criados (is_self=True)")

asyncio.run(main())
