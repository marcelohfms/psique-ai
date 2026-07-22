import asyncio
from dotenv import load_dotenv
load_dotenv()

P1 = "48cf5971-5996-4a93-8337-bf8d8932b8ab"
P2 = "e413609a-1240-4eb1-8bb2-ea3216275ebb"

async def main():
    from app.database import get_supabase

    client = await get_supabase()

    for pid in (P1, P2):
        print(f"\n=== PATIENT {pid} ===")
        p = await client.from_("patients").select("*").eq("id", pid).execute()
        print(p.data)

        print("--- patient_contacts ---")
        pc = await client.from_("patient_contacts").select("*").eq("patient_id", pid).execute()
        print(pc.data)

        print("--- appointments ---")
        appts = await client.from_("appointments").select("appointment_id, start_time, status, doctor_id, consultation_type, booking_fee_paid_at, booking_fee_waived, created_at").eq("patient_id", pid).order("start_time").execute()
        for a in appts.data:
            print(a)

asyncio.run(main())
