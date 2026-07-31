"""Levanta o que falta para registrar o pagamento da Luiza Siqueira Barbosa:
o link do comprovante no Drive, o evento de 06/05 14h na agenda da Dra. Bruna
(se a clínica lançou manualmente) e o valor esperado para aquela data."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "558191183875"
PATIENT_ID = "8a413411-f5ca-431a-aa46-5ede00a5b766"


async def main():
    from app.database import get_supabase
    from app.google_calendar import _credentials
    from app.graph.tools import _expected_consultation_amount
    from googleapiclient.discovery import build

    client = await get_supabase()

    print("=== comprovantes na conversa (conteúdo completo) ===")
    msgs = (await client.from_("messages").select("*").eq("phone", PHONE).ilike(
        "content", "%COMPROVANTE DE PAGAMENTO%").order("created_at").execute()).data
    for m in msgs:
        dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        print(f"\n  {dt}\n  {m['content']}")

    print("\n=== paciente e contato ===")
    p = (await client.from_("patients").select("*").eq("id", PATIENT_ID).single().execute()).data
    print(" ", {k: p.get(k) for k in ("name", "birth_date", "doctor_id", "custom_price")})
    pcs = (await client.from_("patient_contacts").select("*, contacts(*)").eq(
        "patient_id", PATIENT_ID).execute()).data
    for pc in pcs:
        c = pc.get("contacts") or {}
        print(f"  role={pc['role']:12} contact_id={pc['contact_id']} phone={c.get('phone')} nome={c.get('name')}")

    print("\n=== agenda Dra. Bruna — 06/05/2026 ===")
    doc = (await client.from_("doctors").select("*").ilike("name", "%Bruna%").single().execute()).data
    svc = build("calendar", "v3", credentials=_credentials())
    res = svc.events().list(
        calendarId=doc["agenda_id"], timeMin="2026-05-06T00:00:00-03:00",
        timeMax="2026-05-07T00:00:00-03:00", singleEvents=True,
        showDeleted=True, orderBy="startTime").execute()
    for e in res.get("items", []):
        print(f"  {e.get('status'):9} | {e.get('id')} | {(e.get('start') or {}).get('dateTime')} | {e.get('summary')}")

    start = datetime(2026, 5, 6, 14, 0, tzinfo=TZ)
    bd = datetime.strptime(p["birth_date"], "%d/%m/%Y")
    age = start.year - bd.year - ((start.month, start.day) < (bd.month, bd.day))
    print(f"\n=== preço esperado em 06/05/2026 (idade {age}, acompanhamento, Bruna) ===")
    print("  R$", _expected_consultation_amount("bruna", age, "acompanhamento", start,
                                                price_override=p.get("custom_price")))
    print("  doctor_id Bruna:", doc["doctor_id"])

asyncio.run(main())
