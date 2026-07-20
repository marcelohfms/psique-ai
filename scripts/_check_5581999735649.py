import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone, _phone_variants
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    phone = "5581999735649"
    variants = _phone_variants(phone)
    print(f"Variants tried: {variants}")

    users = await get_users_by_phone(phone)
    print(f"\n=== users: {len(users) if users else 0} ===")
    if users:
        for u in users:
            print(f"  id={u['id']} name={u.get('name')} patient_name={u.get('patient_name')} is_patient={u.get('is_patient')}")
            print(f"  is_returning_patient={u.get('is_returning_patient')} booking_fee_waived={u.get('booking_fee_waived')}")

    for v in variants:
        msgs = await client.from_("messages").select("*").eq("phone", v).order("created_at", desc=False).execute()
        print(f"\n=== phone={v}: {len(msgs.data)} mensagens ===")
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:300]}")

    if users:
        for u in users:
            appts = await client.from_("appointments").select("*").eq("patient_id", u["id"]).order("start_time", desc=False).execute()
            print(f"\n=== appointments for patient_id {u['id']}: {len(appts.data)} ===")
            for a in appts.data:
                start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                print(f"\n  start={start} | status={a['status']} | modality={a.get('modality')} | appointment_id={a.get('appointment_id')}")
                print(f"  booking_fee_paid_at={a.get('booking_fee_paid_at')} | booking_fee_waived={a.get('booking_fee_waived')}")
                print(f"  created_at={a.get('created_at')} | updated_at={a.get('updated_at')}")
                print(f"  full row: {a}")

    for v in variants:
        events = await client.from_("events").select("*").eq("phone", v).order("created_at", desc=False).execute()
        print(f"\n=== events for phone {v}: {len(events.data)} ===")
        for e in events.data:
            dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {e.get('event_type')} | {e.get('metadata')}")

asyncio.run(main())
