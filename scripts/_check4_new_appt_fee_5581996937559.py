import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for rid in ("017c1374-fc1a-4597-b681-74271166b88c", "cc35bedc-cd99-4d3c-8f01-b5786b48c89f"):
        appt = await client.from_("appointments").select("*").eq("id", rid).maybe_single().execute()
        a = appt.data
        print(f"--- {rid} ---")
        for k in ("status","start_time","booking_fee_paid_at","booking_fee_waived","custom_price","payment_reminder_sent_at","reschedule_requested_at","reschedule_initiated_by"):
            print(f"  {k}: {a.get(k)}")

asyncio.run(main())
