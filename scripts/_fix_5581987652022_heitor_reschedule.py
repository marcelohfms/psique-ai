"""Fix Ana Cláudia's (5581987652022) agendamento stuck in pending_reschedule."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    patient_id = "1ef19604-d0cb-4cca-991f-44b1753d0e7a"

    # Fetch the stuck appointment
    appts = await client.table("appointments").select("*").eq("patient_id", patient_id).execute()

    if not appts.data:
        print("❌ Nenhum agendamento encontrado para Heitor")
        return

    appt = appts.data[0]
    print(f"📋 Agendamento atual:")
    print(f"  ID: {appt['id']}")
    print(f"  Status: {appt['status']}")
    print(f"  Data: {appt['start_time']}")

    if appt['status'] == "pending_reschedule":
        # Unlock it by setting status back to confirmed
        print(f"\n✅ Atualizando status de pending_reschedule para confirmed...")

        result = await client.table("appointments").update({
            "status": "scheduled",  # Valid status: scheduled, completed, canceled, blocked, pending_reschedule
            "consultation_type": "primeira_consulta",  # First appointment for Heitor
            "updated_at": datetime.now(TZ).isoformat()
        }).eq("id", appt['id']).execute()

        if result.data:
            print(f"✅ Agendamento restaurado!")
            print(f"  Nova data: {result.data[0]['start_time']}")
            print(f"  Novo status: {result.data[0]['status']}")
        else:
            print("❌ Falha ao atualizar")

asyncio.run(main())
