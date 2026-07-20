import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in ["5581996332827@s.whatsapp.net", "558196332827@s.whatsapp.net"]:
        users = await get_users_by_phone(phone)
        print(f"=== {phone}: {len(users) if users else 0} user(s) ===")
        if not users:
            continue
        now = datetime.now(TZ)
        for u in users:
            print(f"Paciente: {u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')} | id={u['id']}")
            appts = await client.from_("appointments").select("*").eq("user_id", u["id"]).order("start_time", desc=True).limit(5).execute()
            for a in appts.data:
                start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                print(f"\n  {start} | status={a['status']} | modality={a.get('modality')} | id={a['id']}")
                print(f"  booking_fee_paid={a.get('booking_fee_paid_at')} | waived={a.get('booking_fee_waived')} | reminder={a.get('payment_reminder_sent_at')}")

asyncio.run(main())
