import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("558196001122@s.whatsapp.net")
    for u in users:
        print(f"name={u.get('name')} | patient_name={u.get('patient_name')} | is_patient={u.get('is_patient')}")
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, status, booking_fee_paid_at, modality"
        ).eq("user_id", u["id"]).order("start_time", desc=True).limit(3).execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            taxa = "✅" if a.get("booking_fee_paid_at") else "⚠️"
            print(f"  {start} | {a['status']} | {a.get('modality')} | taxa={taxa} | {a['appointment_id']}")

asyncio.run(main())
