import asyncio
from dotenv import load_dotenv
load_dotenv()

LAILA = "ae408243-53d1-4b62-88a5-f323a6d710df"
SUZI = "3b0a5557-bef2-4c22-b416-5f564deb7592"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for name, pid in [("LAILA", LAILA), ("SUZI", SUZI)]:
        print(f"\n===== APPOINTMENTS {name} ({pid}) =====")
        appts = await client.table("appointments").select("*").eq("patient_id", pid).order("start_time").execute()
        for a in appts.data:
            print(f" id={a.get("appointment_id")}")
            print(f"   at={a.get('start_time')} status={a.get('status')} doctor={a.get('doctor')} modality={a.get('modality')}")
            print(f"   fee_paid_at={a.get('booking_fee_paid_at')} fee_waived={a.get('booking_fee_waived')} custom_price={a.get('custom_price')}")
            print(f"   gcal={a.get('google_event_id')} created={a.get('created_at')}")

        pass

    print("\n\n===== MID MESSAGES (06-23 to 08-17) =====")
    for phone in ["558196962165"]:
        msgs = await client.table("messages").select("*").eq("phone", phone).gte("created_at", "2026-06-23").lte("created_at", "2026-08-18").order("created_at").execute()
        for m in msgs.data:
            print(f"[{m.get('created_at')}] {m.get('role')}: {(m.get('content') or '')[:250]}")

asyncio.run(main())
