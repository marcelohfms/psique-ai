import asyncio
from dotenv import load_dotenv
load_dotenv()

OLD_ROW = "017c1374-fc1a-4597-b681-74271166b88c"
NEW_ROW = "cc35bedc-cd99-4d3c-8f01-b5786b48c89f"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    old = await client.from_("appointments").select("booking_fee_paid_at").eq("id", OLD_ROW).maybe_single().execute()
    paid_at = old.data.get("booking_fee_paid_at")
    print(f"Taxa original paga em: {paid_at}")
    if not paid_at:
        print("Nada a fazer — taxa original não estava paga.")
        return

    await client.from_("appointments").update({"booking_fee_paid_at": paid_at}).eq("id", NEW_ROW).execute()
    print(f"booking_fee_paid_at copiado para o novo agendamento ({NEW_ROW}).")

    check = await client.from_("appointments").select("booking_fee_paid_at, booking_fee_waived").eq("id", NEW_ROW).maybe_single().execute()
    print("Confirmação:", check.data)

asyncio.run(main())
