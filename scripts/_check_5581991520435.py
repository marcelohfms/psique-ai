import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    users = await get_users_by_phone("5581991520435@s.whatsapp.net")
    client = await get_supabase()
    for u in users:
        print(f"{u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')}")
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, status, modality, booking_fee_paid_at, doctor_id"
        ).eq("user_id", u["id"]).eq("status", "scheduled").order("start_time").execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            duration = int((datetime.fromisoformat(a["end_time"]) - datetime.fromisoformat(a["start_time"])).seconds / 60)
            taxa = "paga" if a.get("booking_fee_paid_at") else "pendente"
            print(f"  {start} ({duration}min) | {a.get('modality') or 'modalidade n/d'} | taxa={taxa} | {a['appointment_id']}")

asyncio.run(main())
