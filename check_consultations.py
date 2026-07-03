import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_consultations():
    from supabase import AsyncClient, acreate_client
    from app.database import get_user_by_phone

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    phone = "5581996937559"

    # Get user/patient info
    user = await get_user_by_phone(phone)
    if not user:
        print(f"❌ Nenhum paciente encontrado para {phone}")
        return

    patient_id = user.get("id")
    patient_name = user.get("patient_name") or user.get("name")

    print(f"✓ Paciente: {patient_name} (ID: {patient_id})")
    print()

    # Get all appointments
    result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, status, reschedule_requested_at, "
        "booking_fee_paid_at, booking_fee_waived"
    ).eq("patient_id", patient_id).order("start_time").execute()

    appointments = result.data or []

    print(f"Total de consultas: {len(appointments)}")
    print()

    for i, appt in enumerate(appointments, 1):
        start = appt.get("start_time", "").split("T")[0] if appt.get("start_time") else "N/A"
        status = appt.get("status")
        fee_paid = appt.get("booking_fee_paid_at")
        fee_waived = appt.get("booking_fee_waived")
        reschedule_requested = appt.get("reschedule_requested_at")

        print(f"{i}. {start} | Status: {status}")
        if fee_paid:
            print(f"   → Taxa paga: {fee_paid.split('T')[0]}")
        if fee_waived:
            print(f"   → Taxa dispensada: SIM")
        if reschedule_requested:
            print(f"   → Reagendamento solicitado: {reschedule_requested.split('T')[0]}")
        print()

asyncio.run(check_consultations())
