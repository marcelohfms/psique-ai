import asyncio
from dotenv import load_dotenv
load_dotenv()

JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Pacientes do Dr. Júlio com primeira_consulta AGENDADA (excluir esses)
    r = await client.from_("appointments").select("patient_id").eq("status", "scheduled").eq("consultation_type", "primeira_consulta").execute()
    excluded_ids = {a["patient_id"] for a in r.data if a.get("patient_id")}
    print(f"Excluídos (primeira_consulta agendada): {len(excluded_ids)}")

    # Todos os pacientes do Dr. Júlio
    p = await client.from_("patients").select("id, name, is_returning_patient").eq("doctor_id", JULIO_ID).execute()
    print(f"Total pacientes Dr. Júlio: {len(p.data)}")

    to_update = [x["id"] for x in p.data if x["id"] not in excluded_ids and x.get("is_returning_patient") is not True]
    print(f"Precisam atualização: {len(to_update)}")

    if to_update:
        result = await client.from_("patients").update({"is_returning_patient": True}).in_("id", to_update).execute()
        print(f"Atualizados: {len(result.data)}")

asyncio.run(main())
