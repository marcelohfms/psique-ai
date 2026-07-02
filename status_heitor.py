import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def check_heitor_status():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    phone = "5581996937559"

    # Get user/patient info
    from app.database import get_user_by_phone
    user = await get_user_by_phone(phone)

    if not user:
        print(f"❌ Nenhum paciente encontrado para {phone}")
        return

    patient_id = user.get("id")
    patient_name = user.get("patient_name") or user.get("name")

    print(f"📋 PACIENTE: {patient_name} (ID: {patient_id})")
    print(f"📱 TELEFONE: {phone}")
    print()

    # Get all appointments (including past)
    result = await client.from_("appointments").select(
        "*"
    ).eq("patient_id", patient_id).order("start_time").execute()

    appointments = result.data or []

    print(f"Total de agendamentos: {len(appointments)}\n")

    for i, appt in enumerate(appointments, 1):
        start_iso = appt.get("start_time", "")
        start_recife = datetime.fromisoformat(start_iso).astimezone(TZ) if start_iso else None
        start_formatted = start_recife.strftime("%d/%m/%Y %H:%M") if start_recife else "N/A"

        status = appt.get("status")
        reschedule_requested = appt.get("reschedule_requested_at")
        fee_paid = appt.get("booking_fee_paid_at")
        fee_waived = appt.get("booking_fee_waived")

        print(f"{i}. {start_formatted} | Status: {status}")
        print(f"   ID: {appt.get('appointment_id')}")

        if reschedule_requested:
            reschedule_dt = datetime.fromisoformat(reschedule_requested).astimezone(TZ)
            print(f"   ⏰ Reagendamento solicitado: {reschedule_dt.strftime('%d/%m/%Y %H:%M')}")

        if fee_paid:
            fee_dt = datetime.fromisoformat(fee_paid).astimezone(TZ)
            print(f"   💳 Taxa paga: {fee_dt.strftime('%d/%m/%Y')}")

        if fee_waived:
            print(f"   ✅ Taxa dispensada")

        print()

asyncio.run(check_heitor_status())
