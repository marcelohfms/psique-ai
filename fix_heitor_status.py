import asyncio
import os
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def fix():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    appt_id = "opsndh1sv1pkgbic4tmul1ihm8"

    # Get current appointment
    result = await client.from_("appointments").select("*").eq("appointment_id", appt_id).execute()
    appt = result.data[0]

    print("ANTES:")
    print(f"  Status: {appt.get('status')}")
    print(f"  reschedule_requested_at: {appt.get('reschedule_requested_at')}")
    print()

    # Update to pending_reschedule with reschedule_requested_at
    # Use the date when reschedule was originally requested (30/06 12:52)
    reschedule_requested_time = datetime(2026, 6, 30, 12, 52, 8).replace(tzinfo=TZ).isoformat()

    await client.from_("appointments").update({
        "status": "pending_reschedule",
        "reschedule_requested_at": reschedule_requested_time,
    }).eq("appointment_id", appt_id).execute()

    # Verify
    result = await client.from_("appointments").select("*").eq("appointment_id", appt_id).execute()
    appt = result.data[0]

    print("DEPOIS:")
    print(f"  Status: {appt.get('status')}")
    print(f"  reschedule_requested_at: {appt.get('reschedule_requested_at')}")
    print()
    print("✅ Heitor agora está em pending_reschedule aguardando confirmação do paciente")

asyncio.run(fix())
