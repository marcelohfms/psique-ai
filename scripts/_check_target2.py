import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()
    ids = ["1cee1241-a23b-4db8-b67c-ce0496513ca2","f2be56bb-aa64-476e-8d1a-8d85b3aa861a"]
    for uid in ids:
        appts = await client.from_("appointments").select("*").eq("user_id", uid).order("start_time", desc=True).execute()
        print(f"=== user {uid}: {len(appts.data)} appt(s) ===")
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"  {start} | status={a['status']} | modality={a.get('modality')} | id={a['id']}")
            print(f"    fee_paid={a.get('booking_fee_paid_at')} | waived={a.get('booking_fee_waived')} | reminder={a.get('payment_reminder_sent_at')} | patient_name={a.get('patient_name')}")
            print(f"    doctor={a.get('doctor_name')} | created={a.get('created_at')}")

asyncio.run(main())
