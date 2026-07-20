"""
One-off: marca manualmente o 2º slot (20h-21h) da primeira consulta dividida
da paciente Nara Freitas Carvalho como 'completed', pois a mãe (Anselmo) quer
agendar a próxima consulta agora e a automação horária ainda não chegou nas
24h necessárias. Replica a mesma lógica de scripts/complete_appointments.py
para is_returning_patient, sem disparar o template de pós-consulta (a
atendente já está em contato com a mãe).
"""
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

APPOINTMENT_ID = "25bea5ce-c333-4d04-a15b-784f7fadab98"
PATIENT_ID = "73b55781-9c51-4aac-b712-294a8954048d"


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    now_iso = datetime.now(timezone.utc).isoformat()

    await (
        client.from_("appointments")
        .update({"status": "completed", "updated_at": now_iso})
        .eq("id", APPOINTMENT_ID)
        .execute()
    )
    print(f"Appointment {APPOINTMENT_ID} marcado como completed.")

    remaining = await (
        client.from_("appointments")
        .select("id")
        .eq("patient_id", PATIENT_ID)
        .eq("consultation_type", "primeira_consulta")
        .eq("status", "scheduled")
        .execute()
    )
    print(f"Slots de primeira_consulta ainda scheduled: {len(remaining.data or [])}")

    if not remaining.data:
        await (
            client.from_("patients")
            .update({"is_returning_patient": True})
            .eq("id", PATIENT_ID)
            .execute()
        )
        print(f"Patient {PATIENT_ID} marcado como is_returning_patient=True.")
    else:
        print("Ainda há slots pendentes — is_returning_patient NÃO alterado.")


asyncio.run(main())
