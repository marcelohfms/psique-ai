import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    appt = await client.table("appointments").select("*").eq("appointment_id","bdllbkc5qke95ldrl6r8bqk634").maybe_single().execute()
    print("=== 01/10 APPOINTMENT (all columns) ===")
    for k,v in appt.data.items():
        print(f"  {k}: {v}")
    # events for this patient recently
    print("\n=== EVENTS (last 20) ===")
    ev = await client.table("events").select("*").eq("patient_id","2642269e-5ae2-4d88-8866-ab0a154554cd").order("created_at", desc=True).limit(20).execute()
    for e in ev.data:
        print(f" [{e.get('created_at')}] {e.get('event_type')}: {str(e.get('metadata'))[:180]}")

asyncio.run(main())
