import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("558192191111@s.whatsapp.net")
    for u in users:
        print(f"{u.get('patient_name') or u.get('name')} | id={u['id']}")
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, status, consultation_type"
        ).eq("user_id", u["id"]).order("start_time", desc=True).limit(5).execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"  {start} | {a['status']} | {a['consultation_type']} | {a['appointment_id']}")

asyncio.run(main())
