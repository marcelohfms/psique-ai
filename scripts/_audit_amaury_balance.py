"""Amaury (558199480798): pagou R$100 de taxa (25/06, reenviado 26/06) + R$450 em
01/07. O sistema deixou a consulta como parcial (paid_at vazio). Este script diz
quanto o próprio motor de preços da Eva esperava para essa consulta."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PATIENT_ID = "11d9b47a-4aab-4511-b993-17321c318112"


async def main():
    from app.database import get_supabase, DOCTOR_NAMES
    from app.graph.tools import _expected_consultation_amount

    client = await get_supabase()
    appts = (await client.from_("appointments").select("*").eq(
        "patient_id", PATIENT_ID).order("start_time").execute()).data

    patient = (await client.from_("patients").select("*").eq(
        "id", PATIENT_ID).single().execute()).data
    bd = datetime.strptime(patient["birth_date"], "%d/%m/%Y")

    for a in appts:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ)
        age = start.year - bd.year - ((start.month, start.day) < (bd.month, bd.day))
        doctor_key = DOCTOR_NAMES.get(a.get("doctor_id", ""), "")
        expected = _expected_consultation_amount(
            doctor_key, age, a.get("consultation_type"), start,
            price_override=patient.get("custom_price"),
        )
        print(f"{start:%d/%m/%Y %H:%M} | {a['status']:10} | {doctor_key or '?':6} | idade={age} "
              f"| tipo={a.get('consultation_type')} | esperado(PIX)=R$ {expected}")
        print(f"    taxa={a.get('booking_fee_paid_at')} quitado={a.get('paid_at')} "
              f"isento={a.get('booking_fee_waived')}")

    print("\nPagamentos informados pela clínica para a consulta de 01/07: "
          "R$ 100,00 (taxa) + R$ 450,00 = R$ 550,00")

asyncio.run(main())
