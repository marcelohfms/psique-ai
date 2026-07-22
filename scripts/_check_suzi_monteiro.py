import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_ID = "3b0a5557-bef2-4c22-b416-5f564deb7592"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    r = await client.from_("patients").select("*").eq("id", PATIENT_ID).execute()
    print("=== patients ===")
    for p in r.data:
        for k, v in p.items():
            print(f"  {k}: {v}")

    r = await client.from_("patient_contacts").select("*").eq("patient_id", PATIENT_ID).execute()
    print("\n=== patient_contacts ===")
    for pc in r.data:
        for k, v in pc.items():
            print(f"  {k}: {v}")
        contact_id = pc.get("contact_id")
        if contact_id:
            rc = await client.from_("contacts").select("*").eq("id", contact_id).execute()
            for c in rc.data:
                print("  -- linked contact --")
                for k, v in c.items():
                    print(f"    {k}: {v}")

    r = await client.from_("appointments").select("*").eq("patient_id", PATIENT_ID).order("start_time", desc=True).limit(10).execute()
    print("\n=== appointments (last 10) ===")
    for a in r.data:
        print(f"  {a.get('start_time')} -> {a.get('end_time')} | status={a.get('status')} | doctor_id={a.get('doctor_id')} | consultation_type={a.get('consultation_type')} | appointment_id={a.get('appointment_id')}")

asyncio.run(main())
