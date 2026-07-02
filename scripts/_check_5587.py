import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    users = await get_users_by_phone("5587981054742@s.whatsapp.net")
    print("Usuários:")
    for u in users:
        print(f"  {u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')} | doctor_id={u.get('doctor_id')}")

    client = await get_supabase()
    for u in users:
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, status, doctor_id"
        ).eq("user_id", u["id"]).order("start_time", desc=True).limit(5).execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            end = datetime.fromisoformat(a["end_time"]).astimezone(TZ)
            duration = int((datetime.fromisoformat(a["end_time"]).astimezone(TZ) - datetime.fromisoformat(a["start_time"]).astimezone(TZ)).seconds / 60)
            print(f"  {start} ({duration}min) | {a['status']} | {a['appointment_id']}")

asyncio.run(main())
