import asyncio
from dotenv import load_dotenv
load_dotenv()

PID = "d640f9e3-95c3-4790-b381-b930186e8f8c"


async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    print("=== APPOINTMENTS do Marcelo Filho ===")
    a = await client.from_("appointments").select("*").eq("patient_id", PID).order("start_time").execute()
    for x in a.data:
        st = datetime.fromisoformat(x["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        en = datetime.fromisoformat(x["end_time"]).astimezone(TZ).strftime("%H:%M")
        print(f"\n  {st}-{en} | status={x['status']} | modality={x.get('modality')} | type={x.get('consultation_type')}")
        for k in ("appointment_id", "doctor_id", "user_id", "contact_id", "booking_fee_paid_at",
                  "booking_fee_waived", "paid_at", "created_at", "updated_at", "pending_reschedule",
                  "confirmed_at", "payment_id"):
            print(f"      {k}={x.get(k)}")

    print("\n=== CONTACTS ===")
    try:
        c = await client.from_("contacts").select("*").ilike("phone", "%999865181%").execute()
        print(c.data)
    except Exception as e:
        print("contacts:", e)
        c2 = await client.from_("contacts").select("*").limit(1).execute()
        print("cols:", list(c2.data[0].keys()) if c2.data else "vazio")

    print("\n=== DOCTORS ===")
    d = await client.from_("doctors").select("*").execute()
    for x in d.data:
        print(" ", x)


asyncio.run(main())
