"""Investiga a marcação do paciente Marcelo Filho (5581999865181):
a consulta de amanhã (06/08/2026) não aparece no Google Calendar.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581999865181"


async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    print("=== USERS por telefone ===")
    users = await client.from_("users").select("*").ilike("number", f"%{PHONE[-8:]}%").execute()
    for u in users.data:
        print(f"  user id={u.get('id')} phone={u.get('phone')} name={u.get('name')}")

    user_ids = [u["id"] for u in users.data]

    print("\n=== PATIENTS ===")
    pats = []
    if user_ids:
        p = await client.from_("patients").select("*").in_("user_db_id", user_ids).execute()
        pats = p.data
    pn = await client.from_("patients").select("*").ilike("name", "%Marcelo%").execute()
    seen = {x["patient_id"]: x for x in pats}
    for x in pn.data:
        seen.setdefault(x["patient_id"], x)
    for x in seen.values():
        print(f"  patient_id={x['patient_id']} name={x.get('name')} user_db_id={x.get('user_db_id')} doctor={x.get('doctor_id')}")

    print("\n=== APPOINTMENTS ===")
    pids = list(seen.keys())
    if pids:
        appts = await client.from_("appointments").select("*").in_("patient_id", pids).order("start_time").execute()
        for a in appts.data:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"  {st} | status={a['status']} | doctor={a.get('doctor_id')} | patient={a.get('patient_id')}")
            print(f"      appointment_id={a['appointment_id']}")
            print(f"      fee_paid_at={a.get('booking_fee_paid_at')} waived={a.get('booking_fee_waived')} created_at={a.get('created_at')}")


asyncio.run(main())
