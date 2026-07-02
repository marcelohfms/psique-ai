import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    appt = await client.from_("appointments").select(
        "appointment_id, paid_at, booking_fee_paid_at, status"
    ).eq("appointment_id", "vkb26h65pguje8k0q0hqu7te98").maybe_single().execute()
    print("APPT STATE:", appt.data)
    evs = await client.from_("events").select("*").eq(
        "phone", "5581999480798"
    ).eq("event_type", "payment_receipt_registered").order("created_at", desc=True).limit(1).execute()
    print("LAST PAYMENT EVENT:", evs.data)

asyncio.run(main())
