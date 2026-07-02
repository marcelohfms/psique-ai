import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    appts = await client.from_("appointments").select(
        "appointment_id, start_time, status, doctor_id, users(name, patient_name)"
    ).gte("start_time", "2026-06-16T19:00:00+00:00") \
     .lte("start_time", "2026-06-16T19:01:00+00:00") \
     .execute()

    if not appts.data:
        print("Nenhum agendamento encontrado para 16/06 às 16:00")
    for a in appts.data:
        user = a.get("users") or {}
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        print(f"{start} | {a['status']} | {user.get('patient_name') or user.get('name')} | {a['appointment_id']}")

asyncio.run(main())
