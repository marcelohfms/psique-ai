import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def check():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    print("=== AGENDAMENTOS DE HOJE (02/07) COM HISTÓRICO DE REAGENDAMENTO ===\n")

    # Get all appointments for today
    today_start = "2026-07-02T00:00:00"
    today_end = "2026-07-02T23:59:59"

    result = await client.from_("appointments").select(
        "*"
    ).gte("start_time", today_start).lte("start_time", today_end).order("start_time").execute()

    appointments = result.data or []

    print(f"Total de agendamentos para hoje: {len(appointments)}\n")

    for i, appt in enumerate(appointments, 1):
        appt_id = appt.get("appointment_id")
        status = appt.get("status")
        start_iso = appt.get("start_time", "")
        start_recife = datetime.fromisoformat(start_iso).astimezone(TZ) if start_iso else None
        start_formatted = start_recife.strftime("%H:%M") if start_recife else "N/A"

        reschedule_requested = appt.get("reschedule_requested_at")
        fee_paid = appt.get("booking_fee_paid_at")
        fee_waived = appt.get("booking_fee_waived")

        # Get patient name
        patient_id = appt.get("patient_id")
        patient_result = await client.from_("patients").select("name").eq("id", patient_id).single().execute()
        patient_name = patient_result.data.get("name") if patient_result.data else "N/A"

        print(f"{i}. {start_formatted} | {patient_name}")
        print(f"   ID: {appt_id}")
        print(f"   Status: {status}")

        if reschedule_requested:
            reschedule_dt = datetime.fromisoformat(reschedule_requested).astimezone(TZ)
            print(f"   ⚠️  Reagendamento em andamento! Solicitado: {reschedule_dt.strftime('%d/%m %H:%M')}")
        else:
            print(f"   ✅ Reagendamento concluído (ou nunca foi remarcado)")

        if fee_paid:
            fee_dt = datetime.fromisoformat(fee_paid).astimezone(TZ)
            print(f"   💳 Taxa: Paga em {fee_dt.strftime('%d/%m')}", end="")
            if fee_waived:
                print(" - DISPENSADA")
            else:
                print()

        print()

asyncio.run(check())
