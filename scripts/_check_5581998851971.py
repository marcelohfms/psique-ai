import asyncio
from dotenv import load_dotenv
load_dotenv()


async def main():
    import sys
    sys.path.insert(0, "dashboard")
    import attendant_db
    from db_client import get_client
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    phone = "5581988851971"
    resolved = await attendant_db.resolve_contact_and_patients(phone)
    print("=== RESOLVE ===")
    print(resolved)

    client = await get_client()
    patient_ids = [p["id"] for p in resolved.get("patients", [])]
    print("\npatient_ids:", patient_ids)

    if patient_ids:
        appts = await (
            client.from_("appointments")
            .select("appointment_id, patient_id, start_time, status, doctor_id, "
                    "paid_at, booking_fee_paid_at, booking_fee_waived, consultation_type")
            .in_("patient_id", patient_ids)
            .order("start_time", desc=True)
            .limit(10)
            .execute()
        )
        print("\n=== APPOINTMENTS ===")
        for a in appts.data or []:
            st = a["start_time"]
            try:
                st = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
            print(f"  {a['appointment_id']} | {st} | status={a['status']} | "
                  f"paid_at={a.get('paid_at')} | booking_fee_paid_at={a.get('booking_fee_paid_at')} | "
                  f"booking_fee_waived={a.get('booking_fee_waived')} | type={a.get('consultation_type')}")

    print("\n=== EVENTS (all, last 20 per variant) ===")
    for variant in attendant_db._phone_variants(phone):
        events = await (
            client.from_("events")
            .select("*")
            .eq("phone", variant)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        for e in events.data or []:
            print(f"  {variant} | {e['created_at']} | {e['event_type']} | {e.get('metadata')}")

asyncio.run(main())
