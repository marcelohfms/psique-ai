import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    await client.from_("appointments").update({
        "booking_fee_paid_at": "2026-05-28T03:11:50.184492+00:00",
    }).eq("appointment_id", "uhgu19ld7ukt94esacei01p5k8").execute()
    print("✅ booking_fee_paid_at transferido para 30/06")

    # Confirma
    a = await client.from_("appointments").select(
        "booking_fee_paid_at, status"
    ).eq("appointment_id", "uhgu19ld7ukt94esacei01p5k8").single().execute()
    print(f"   booking_fee_paid_at = {a.data['booking_fee_paid_at']}")

asyncio.run(main())
