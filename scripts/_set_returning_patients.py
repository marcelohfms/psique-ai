import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Pacientes com pelo menos uma consulta concluída
    r = await client.from_("appointments").select("patient_id").eq("status", "completed").execute()
    patient_ids = sorted({a["patient_id"] for a in r.data if a.get("patient_id")})
    print(f"Pacientes com consulta concluída: {len(patient_ids)}")

    # Verifica quais já estão com is_returning_patient != True
    p = await client.from_("patients").select("id, is_returning_patient").in_("id", patient_ids).execute()
    to_update = [x["id"] for x in p.data if x.get("is_returning_patient") is not True]
    print(f"Precisam de atualização: {len(to_update)}")

    if to_update:
        result = await client.from_("patients").update({"is_returning_patient": True}).in_("id", to_update).execute()
        print(f"Atualizados: {len(result.data)}")
    else:
        print("Nenhum paciente precisava de atualização.")

asyncio.run(main())
