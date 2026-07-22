import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
BOOKING_FEE = 100

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}


def _fmt(iso):
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m/%Y %H:%M")


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Find patient(s) matching "Gustavo Lapenda"
    pr = await client.from_("patients").select(
        "id, name, custom_price, patient_contacts(is_self, contacts(phone, name))"
    ).ilike("name", "%lapenda%").execute()

    patients = pr.data or []
    if not patients:
        print("Nenhum paciente com nome contendo 'lapenda' encontrado.")
        # fallback: try gustavo
        pr = await client.from_("patients").select(
            "id, name, custom_price, patient_contacts(is_self, contacts(phone, name))"
        ).ilike("name", "%gustavo%").execute()
        patients = pr.data or []
        print(f"Fallback 'gustavo': {[p['name'] for p in patients]}")
        return

    for p in patients:
        pid = p["id"]
        price = p.get("custom_price")
        print("=" * 60)
        print(f"Paciente: {p['name']}  (patient_id={pid})")
        print(f"custom_price: {price}")
        pcs = p.get("patient_contacts") or []
        for pc in pcs:
            c = pc.get("contacts") or {}
            print(f"  contato: {c.get('name')} / {c.get('phone')} (is_self={pc.get('is_self')})")

        appts = await client.from_("appointments").select(
            "appointment_id, start_time, status, doctor_id, consultation_type, "
            "booking_fee_paid_at, booking_fee_waived, paid_at"
        ).eq("patient_id", pid).order("start_time").execute()

        print(f"\n  Consultas ({len(appts.data or [])}):")
        for a in appts.data or []:
            doctor = DOCTOR_LABELS.get(a.get("doctor_id", ""), "—")
            print(f"   • {_fmt(a['start_time'])} | {a['status']:>12} | {doctor} | {a.get('consultation_type') or ''}")
            print(f"       taxa_paga={_fmt(a.get('booking_fee_paid_at'))}  "
                  f"taxa_dispensada={a.get('booking_fee_waived')}  "
                  f"consulta_paga={_fmt(a.get('paid_at'))}")
        print()


asyncio.run(main())
