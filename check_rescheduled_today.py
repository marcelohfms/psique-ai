import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

async def check_rescheduled():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    today = "2026-07-02"

    print(f"=== AGENDAMENTOS REMARCADOS PARA {today} ===\n")

    # Get appointments scheduled for today
    from datetime import datetime
    today_start = f"{today}T00:00:00"
    today_end = f"{today}T23:59:59"

    result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, status, patient_id, patients(name), "
        "reschedule_requested_at, booking_fee_waived"
    ).gte("start_time", today_start).lte("start_time", today_end).order("start_time").execute()

    appointments = result.data or []

    print(f"Total de agendamentos para hoje: {len(appointments)}\n")

    for appt in appointments:
        start = appt.get("start_time", "")
        status = appt.get("status")
        patient_name = appt.get("patients", {}).get("name") or "N/A"
        reschedule_requested = appt.get("reschedule_requested_at")
        fee_waived = appt.get("booking_fee_waived")

        print(f"• {start} | {patient_name}")
        print(f"  Status: {status}")
        if reschedule_requested:
            print(f"  Reagendamento solicitado: {reschedule_requested}")
        if fee_waived:
            print(f"  Taxa dispensada: SIM")
        print()

    # Agora checando agendamentos com status pending_reschedule (os que foram movidos)
    print(f"\n=== AGENDAMENTOS COM STATUS pending_reschedule ===\n")

    result2 = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, status, patient_id, patients(name)"
    ).eq("status", "pending_reschedule").order("start_time").execute()

    pending = result2.data or []
    print(f"Total com status pending_reschedule: {len(pending)}\n")

    for appt in pending[:10]:  # Show first 10
        start = appt.get("start_time", "")
        patient_name = appt.get("patients", {}).get("name") or "N/A"
        print(f"• {start} | {patient_name}")

asyncio.run(check_rescheduled())
