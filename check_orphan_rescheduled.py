import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_orphan():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    print(f"=== AGENDAMENTOS COM reschedule_requested_at (potencial problema) ===\n")

    # Get all appointments with reschedule_requested_at
    result = await client.from_("appointments").select(
        "appointment_id, start_time, status, patient_id, patients(name), "
        "reschedule_requested_at"
    ).not_.is_("reschedule_requested_at", "null").order("start_time").execute()

    appointments = result.data or []

    print(f"Total de agendamentos com reschedule_requested_at: {len(appointments)}\n")

    for appt in appointments:
        start = appt.get("start_time", "")
        status = appt.get("status")
        patient_name = appt.get("patients", {}).get("name") or "N/A"
        reschedule_requested = appt.get("reschedule_requested_at", "")

        print(f"• {start.split('T')[0]} {start.split('T')[1][:5] if 'T' in start else ''}")
        print(f"  Paciente: {patient_name}")
        print(f"  Status: {status}")
        print(f"  Reagendamento solicitado: {reschedule_requested.split('T')[0] if reschedule_requested else 'N/A'}")
        print()

asyncio.run(check_orphan())
