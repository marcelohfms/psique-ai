import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for appt_id, label in [
        ("v48mev55t4777kq84411bjtvo0", "11/06 (cancelada)"),
        ("uhgu19ld7ukt94esacei01p5k8", "30/06 (ativa)"),
    ]:
        a = await client.from_("appointments").select(
            "appointment_id, status, booking_fee_paid_at, booking_fee_waived, paid_at, payment_reminder_sent_at"
        ).eq("appointment_id", appt_id).single().execute()
        d = a.data
        print(f"\n{label}:")
        print(f"  booking_fee_paid_at:        {d.get('booking_fee_paid_at')}")
        print(f"  booking_fee_waived:         {d.get('booking_fee_waived')}")
        print(f"  paid_at:                    {d.get('paid_at')}")
        print(f"  payment_reminder_sent_at:   {d.get('payment_reminder_sent_at')}")

asyncio.run(main())
