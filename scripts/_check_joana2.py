import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("558799880457@s.whatsapp.net")
    uid = users[0]["id"]

    appts = await client.from_("appointments").select("*").eq("user_id", uid).order("start_time", desc=True).execute()
    now = datetime.now(TZ)
    for a in appts.data:
        created = datetime.fromisoformat(a["created_at"]).astimezone(TZ)
        reminder = datetime.fromisoformat(a["payment_reminder_sent_at"]).astimezone(TZ) if a.get("payment_reminder_sent_at") else None
        print(f"status={a['status']} | booking_fee_paid={a.get('booking_fee_paid_at')} | booking_fee_waived={a.get('booking_fee_waived')}")
        print(f"  created_at:               {created.strftime('%d/%m %H:%M')} (há {now - created})")
        print(f"  payment_reminder_sent_at: {reminder.strftime('%d/%m %H:%M') if reminder else 'NÃO ENVIADO'}")
        if reminder:
            print(f"  lembrete enviado há:      {now - reminder}")

asyncio.run(main())
