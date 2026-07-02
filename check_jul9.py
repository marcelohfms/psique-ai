import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_jul9():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    date = "2026-07-09"

    print(f"=== AGENDAMENTOS PARA {date} ===\n")

    # Get appointments
    from datetime import datetime
    date_start = f"{date}T00:00:00"
    date_end = f"{date}T23:59:59"

    result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, status, patient_id, patients(name), "
        "reschedule_requested_at, booking_fee_waived"
    ).gte("start_time", date_start).lte("start_time", date_end).order("start_time").execute()

    appointments = result.data or []

    print(f"Total de agendamentos: {len(appointments)}\n")

    for appt in appointments:
        start = appt.get("start_time", "")
        status = appt.get("status")
        patient_name = appt.get("patients", {}).get("name") or "N/A"
        appointment_id = appt.get("appointment_id")

        print(f"• {start} | {patient_name} (ID: {appointment_id})")
        print(f"  Status: {status}")
        if appt.get("reschedule_requested_at"):
            print(f"  Reagendamento solicitado: {appt.get('reschedule_requested_at')}")
        print()

asyncio.run(check_jul9())
