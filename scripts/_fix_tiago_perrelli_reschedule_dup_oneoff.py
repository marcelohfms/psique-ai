import asyncio
from dotenv import load_dotenv
load_dotenv()

OLD_ID = "114b6fbc-2fd9-4007-a1b2-96c5fddd9c1f"   # pending_reschedule, 09/07 14:00, taxa paga
NEW_ID = "73956dc7-cd86-4116-8005-4b7acf4c6991"   # scheduled, 09/07 16:00, sem taxa
FEE_PAID_AT = "2026-06-25T17:32:53.907085+00:00"


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    await client.from_("appointments").update({
        "status": "canceled",
        "reschedule_requested_at": None,
    }).eq("id", OLD_ID).execute()
    print("Linha antiga marcada como canceled.")

    await client.from_("appointments").update({
        "booking_fee_paid_at": FEE_PAID_AT,
    }).eq("id", NEW_ID).execute()
    print("Taxa transferida para a linha nova.")

    check = await client.from_("appointments").select("*").in_("id", [OLD_ID, NEW_ID]).execute()
    for a in check.data:
        print(a)

asyncio.run(main())
