"""Verificação dos 3 casos que sobraram, com o histórico informado pela clínica:

  558199480798 Amaury  — R$100 (25/06, reenviado 26/06) + R$450 (01/07); ficou como parcial
  558191183875 Luiza   — R$100 (01/07) + R$600 (15/07); consulta ocorreu 15/07 10h,
                         mas a auditoria não achou NENHUMA consulta no banco
  558191812399         — número genérico da clínica (a atendente manda comprovantes
                         para a pasta do Drive por ali); não é paciente
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")


def _fmt(iso):
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m/%Y %H:%M")


async def dump(client, phone, label):
    from app.patients import get_contact_by_phone, get_patients_by_contact
    print(f"\n{'='*78}\n📞 {phone} — {label}")

    contact = await get_contact_by_phone(phone)
    print(f"  contato: {contact and contact.get('name')} (id={contact and contact.get('id')})")

    patients = await get_patients_by_contact(contact["id"]) if contact else []
    for p in patients:
        print(f"\n  👤 {p.get('name')!r} id={p['id']} nasc={p.get('birth_date')} "
              f"custom_price={p.get('custom_price')} ativo={p.get('active')}")
        appts = await client.from_("appointments").select("*").eq(
            "patient_id", p["id"]).order("start_time").execute()
        if not appts.data:
            print("      (nenhuma consulta no banco)")
        for a in appts.data:
            print(f"      {_fmt(a['start_time'])} | {a['status']:11} | tipo={a.get('consultation_type')} "
                  f"| taxa={_fmt(a.get('booking_fee_paid_at'))} | quitado={_fmt(a.get('paid_at'))} "
                  f"| event={a.get('appointment_id')}")

    evs = await client.from_("events").select("*").eq("phone", phone).eq(
        "event_type", "payment_receipt_registered").order("created_at").execute()
    print(f"\n  💰 payment_receipt_registered ({len(evs.data)}):")
    for e in evs.data:
        d = e.get("data") or {}
        print(f"      {_fmt(e['created_at'])} | {d.get('patient_name')} | R$ {d.get('amount')} "
              f"| {d.get('payment_type')}")


async def main():
    from app.database import get_supabase
    client = await get_supabase()
    await dump(client, "558199480798", "Amaury — parcial em aberto?")
    await dump(client, "558191183875", "Luiza — consulta de 15/07 não existe no banco?")
    await dump(client, "558191812399", "número genérico da clínica")

asyncio.run(main())
