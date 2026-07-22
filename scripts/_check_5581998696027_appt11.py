import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.from_("appointments").select("*").eq("appointment_id","76r2bra61amjkt6o8dck9r39nc").execute()
    for a in res.data:
        for k,v in a.items():
            print(f"  {k}: {v}")
    print("\n=== appts by patient_id for Lara (6b82acdd) ===")
    res2 = await client.from_("appointments").select("appointment_id,start_time,status,paid_at,booking_fee_paid_at,consultation_type,patient_id,user_id").eq("patient_id","6b82acdd-b1d1-4407-b77f-635118ddf194").order("start_time",desc=True).limit(8).execute()
    for a in res2.data:
        print(f"  {a['appointment_id']} | {a['start_time']} | {a['status']} | paid_at={a.get('paid_at')} | bfee={a.get('booking_fee_paid_at')} | pid={a.get('patient_id')} | uid={a.get('user_id')}")

asyncio.run(main())
