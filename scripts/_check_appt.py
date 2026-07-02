import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_user_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    PHONE = "5581995006049@s.whatsapp.net"
    user = await get_user_by_phone(PHONE)
    print(f"Paciente: {user.get('patient_name') or user.get('name')} | doctor_id: {user.get('doctor_id')}")

    client = await get_supabase()
    appts = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, status, doctor_id"
    ).eq("user_id", user["id"]).order("start_time", desc=True).limit(5).execute()

    for a in appts.data:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ)
        end = datetime.fromisoformat(a["end_time"]).astimezone(TZ)
        duration = int((end - start).seconds / 60)
        print(f"  ID: {a['appointment_id']} | {start.strftime('%d/%m/%Y %H:%M')} ({duration}min) | {a['status']}")

asyncio.run(main())
