import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_heitor_appt():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    # O appointment ID que vimos nos eventos
    appointment_id = "opsndh1sv1pkgbic4tmul1ihm8"

    print(f"=== AGENDAMENTO {appointment_id} ===\n")

    result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, status, patient_id, patients(name), "
        "reschedule_requested_at, booking_fee_waived"
    ).eq("appointment_id", appointment_id).execute()

    if not result.data:
        print("Agendamento não encontrado!")
        return

    appt = result.data[0]
    start = appt.get("start_time", "")
    end = appt.get("end_time", "")
    status = appt.get("status")
    patient_name = appt.get("patients", {}).get("name") or "N/A"

    print(f"Paciente: {patient_name}")
    print(f"Início: {start}")
    print(f"Fim: {end}")
    print(f"Status: {status}")
    if appt.get("reschedule_requested_at"):
        print(f"Reagendamento solicitado: {appt.get('reschedule_requested_at')}")
    if appt.get("booking_fee_waived"):
        print(f"Taxa dispensada: SIM")

asyncio.run(check_heitor_appt())
