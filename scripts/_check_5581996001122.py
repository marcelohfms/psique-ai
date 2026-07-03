import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("5581996001122@s.whatsapp.net")
    if not users:
        print("Nenhum usuário encontrado")
        return

    now = datetime.now(TZ)
    for u in users:
        print(f"Paciente: {u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')}")
        appts = await client.from_("appointments").select("*").eq("user_id", u["id"]).order("start_time", desc=True).limit(3).execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            created = datetime.fromisoformat(a["created_at"]).astimezone(TZ)
            reminder = datetime.fromisoformat(a["payment_reminder_sent_at"]).astimezone(TZ) if a.get("payment_reminder_sent_at") else None
            print(f"\n  {start} | status={a['status']} | modality={a.get('modality')}")
            print(f"  booking_fee_paid={a.get('booking_fee_paid_at')} | waived={a.get('booking_fee_waived')}")
            print(f"  created_at: {created.strftime('%d/%m %H:%M')} (há {now - created})")
            print(f"  lembrete:   {reminder.strftime('%d/%m %H:%M') + ' (há ' + str(now - reminder) + ')' if reminder else 'NÃO ENVIADO'}")

asyncio.run(main())
