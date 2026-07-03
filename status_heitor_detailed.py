import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def check():
    from supabase import AsyncClient, acreate_client
    from app.database import get_user_by_phone

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    user = await get_user_by_phone("5581996937559")
    appt_id = "opsndh1sv1pkgbic4tmul1ihm8"

    result = await client.from_("appointments").select("*").eq("appointment_id", appt_id).execute()

    if not result.data:
        print("Agendamento não encontrado")
        return

    appt = result.data[0]

    print(f"=== AGENDAMENTO HEITOR ===\n")
    print(f"ID: {appt.get('appointment_id')}")
    print(f"Status: {appt.get('status')}")
    print()

    start_raw = appt.get("start_time")
    end_raw = appt.get("end_time")

    print(f"Horário (UTC):     {start_raw}")
    print(f"Horário (Recife):  {datetime.fromisoformat(start_raw).astimezone(TZ).strftime('%d/%m/%Y %H:%M:%S')}")
    print()

    if appt.get("reschedule_requested_at"):
        reschedule_at = appt.get("reschedule_requested_at")
        print(f"Reagendamento solicitado: {reschedule_at}")
        print(f"Data/Hora (Recife): {datetime.fromisoformat(reschedule_at).astimezone(TZ).strftime('%d/%m/%Y %H:%M:%S')}")
    else:
        print("Reagendamento solicitado: NÃO")

    print()
    print(f"Taxa paga: {appt.get('booking_fee_paid_at')}")
    print(f"Taxa dispensada: {appt.get('booking_fee_waived')}")

asyncio.run(check())
