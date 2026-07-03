import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Verifica estado atual
    r = await client.from_("users").select("id, number, name, patient_name, is_patient").eq("id", "40880d6a-a927-4f1b-821e-e9f77d3334b0").execute()
    print("Antes:", r.data)

    # Corrige is_patient para False (Felipe é contato, não paciente)
    await client.from_("users").update({"is_patient": False}).eq("id", "40880d6a-a927-4f1b-821e-e9f77d3334b0").execute()

    r = await client.from_("users").select("id, number, name, patient_name, is_patient").eq("id", "40880d6a-a927-4f1b-821e-e9f77d3334b0").execute()
    print("Depois:", r.data)

    # Verifica se há outros registros com is_patient=True onde name != patient_name (inconsistência)
    all_r = await client.from_("users").select("id, number, name, patient_name, is_patient").eq("is_patient", True).execute()
    inconsistent = [u for u in all_r.data if u.get("name") and u.get("patient_name") and u["name"].lower() != u["patient_name"].lower()]
    print(f"\nRegistros is_patient=True com name ≠ patient_name ({len(inconsistent)}):")
    for u in inconsistent:
        print(f"  id={u['id']} | number={u['number']} | name={u['name']} | patient_name={u['patient_name']}")

asyncio.run(main())
