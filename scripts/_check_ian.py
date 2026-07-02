import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    PHONE = "5581988007007@s.whatsapp.net"
    users = await get_users_by_phone(PHONE)
    print(f"Usuários para {PHONE}:")
    for u in users:
        print(f"  id={u['id']} | name={u.get('name')} | patient_name={u.get('patient_name')} | doctor_id={u.get('doctor_id')} | is_patient={u.get('is_patient')}")

    client = await get_supabase()
    for u in users:
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, status, doctor_id"
        ).eq("user_id", u["id"]).order("start_time", desc=True).limit(5).execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ)
            end = datetime.fromisoformat(a["end_time"]).astimezone(TZ)
            duration = int((end - start).seconds / 60)
            print(f"  APPT: {start.strftime('%d/%m/%Y %H:%M')} ({duration}min) | {a['status']} | id={a['appointment_id']}")

asyncio.run(main())
