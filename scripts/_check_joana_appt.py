import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    appt = await client.from_("appointments").select("*") \
        .eq("appointment_id", "abcmv3s0lbtgf8oe8cvp0bu1mg").execute()
    # esse é da Maria Alice, precisamos do da Joana
    appt2 = await client.from_("appointments").select("*") \
        .eq("status", "scheduled").eq("booking_fee_waived", False) \
        .is_("booking_fee_paid_at", "null") \
        .ilike("appointment_id", "%").execute()
    
    # Busca pelo user da Joana
    user = await client.from_("users").select("id").ilike("name", "%joana%").execute()
    uid = user.data[0]["id"] if user.data else None
    print(f"user_id={uid}")
    
    appt3 = await client.from_("appointments").select("*").eq("user_id", uid).single().execute()
    a = appt3.data
    print("\n=== AGENDAMENTO ===")
    for k, v in a.items():
        if v is not None:
            print(f"  {k}: {v}")
    
    now = datetime.now(TZ)
    created = datetime.fromisoformat(a["created_at"]).astimezone(TZ)
    reminder_sent = datetime.fromisoformat(a["payment_reminder_sent_at"]).astimezone(TZ) if a.get("payment_reminder_sent_at") else None
    print(f"\n  Criado há: {now - created}")
    print(f"  Lembrete enviado: {reminder_sent.strftime('%d/%m %H:%M') if reminder_sent else 'NÃO'}")
    if reminder_sent:
        print(f"  Lembrete enviado há: {now - reminder_sent}")

asyncio.run(main())
