import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

PATIENTS = {
    "Leonardo Bezerra Oliveira Felix": "8debbb77-4f59-4026-ab56-26acbbe4f9dc",
    "Paula Muniz Evangelista": "b3c58f9c-a8f3-41a5-8440-ca9bfab9bafc",
    "Isabela Spinelli": "891ac742-116b-4370-a664-265cdb2c7d24",
}


async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for name, pid in PATIENTS.items():
        print(f"\n{'='*70}\n{name}  ({pid})")
        pc = await client.from_("patient_contacts").select(
            "role, is_self, relationship, contacts(name, phone)"
        ).eq("patient_id", pid).execute()
        print("  -- vínculos --")
        for row in pc.data:
            c = row.get("contacts") or {}
            print(f"    role={row['role']:12} is_self={row['is_self']} rel={row.get('relationship')} | {c.get('name')} ({c.get('phone')})")

        appts = await client.from_("appointments").select(
            "start_time, status, booking_fee_paid_at, booking_fee_waived, payment_reminder_sent_at, paid_at"
        ).eq("patient_id", pid).order("start_time", desc=True).limit(5).execute()
        print("  -- consultas (últimas 5) --")
        for a in appts.data:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"    {st} | status={a['status']} | fee_paid={a['booking_fee_paid_at']} | fee_waived={a['booking_fee_waived']} | reminder_sent={a['payment_reminder_sent_at']} | paid_at={a['paid_at']}")


asyncio.run(main())
