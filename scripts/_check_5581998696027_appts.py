import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone, DOCTOR_NAMES
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("5581998696027@s.whatsapp.net")
    print(f"get_users_by_phone -> {len(users)} users")
    for u in users:
        print("  user_id:", u["id"], "name:", u.get("patient_name") or u.get("name"), "doctor:", DOCTOR_NAMES.get(u.get("doctor_id",""),""))
        appts = await client.from_("appointments").select(
            "appointment_id, user_id, start_time, status, paid_at, consultation_type"
        ).eq("user_id", u["id"]).order("start_time", desc=True).limit(8).execute()
        for a in appts.data:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"    {a['appointment_id']} | {st} | status={a['status']} | paid_at={a.get('paid_at')} | type={a.get('consultation_type')}")

    # Also search all appointments on 13/07 for Lara
    print("\n=== appointments on 13/07 ===")
    ds = datetime(2026,7,13,0,0,tzinfo=TZ).isoformat()
    de = datetime(2026,7,13,23,59,tzinfo=TZ).isoformat()
    res = await client.from_("appointments").select("appointment_id, user_id, start_time, status, paid_at").gte("start_time", ds).lte("start_time", de).execute()
    for a in res.data:
        st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m %H:%M")
        print(f"  {a['appointment_id']} | uid={a['user_id']} | {st} | status={a['status']} | paid_at={a.get('paid_at')}")

asyncio.run(main())
