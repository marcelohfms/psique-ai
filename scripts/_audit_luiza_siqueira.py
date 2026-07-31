"""558191183875 — Alexandra Maria da Silva Siqueira / Luiza Siqueira Barbosa.
Enviou um comprovante de R$ 550,00 em 19/06 (duas vezes, o mesmo). Procura esse
pagamento na planilha, a paciente na agenda e qualquer consulta no banco."""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase
    from app.google_sheets import _credentials as sheets_creds
    from app.google_calendar import _credentials as cal_creds
    from googleapiclient.discovery import build

    # ── planilha: alguma linha de junho com 550, ou com o telefone/nome? ──────
    svc = build("sheets", "v4", credentials=sheets_creds())
    rows = svc.spreadsheets().values().get(
        spreadsheetId=os.environ["GOOGLE_SHEETS_PAYMENTS_ID"],
        range="Pagamentos!A:J").execute().get("values", [])
    print("=== planilha: linhas com 'siqueira', 'barbosa', o telefone, ou valor 550 em junho ===")
    for r in rows[1:]:
        line = " | ".join(r)
        low = line.lower()
        if ("siqueira" in low or "barbosa" in low or "91183875" in line
                or ("550" in (r[4] if len(r) > 4 else "") and "/06/2026" in (r[0] if r else ""))):
            print("  " + line)

    # ── agenda: algum evento com o nome dela? ────────────────────────────────
    client = await get_supabase()
    docs = (await client.from_("doctors").select("name, agenda_id").execute()).data
    print("\n=== agenda (jun–ago/2026): eventos com 'siqueira' ou 'barbosa' ===")
    for d in docs:
        res = svc_cal = build("calendar", "v3", credentials=cal_creds()).events().list(
            calendarId=d["agenda_id"], timeMin="2026-06-01T00:00:00-03:00",
            timeMax="2026-09-01T00:00:00-03:00", singleEvents=True,
            showDeleted=True, orderBy="startTime", maxResults=2500).execute()
        for e in res.get("items", []):
            s = (e.get("summary") or "").lower()
            if "siqueira" in s or "barbosa" in s or "luiza" in s or "luíza" in s:
                print(f"  {d['name']:6} | {e.get('status'):9} | {(e.get('start') or {}).get('dateTime')} "
                      f"| {e.get('summary')}")

    # ── banco: qualquer paciente com esse sobrenome ──────────────────────────
    print("\n=== banco: pacientes 'Siqueira'/'Barbosa' e suas consultas ===")
    pats = (await client.from_("patients").select("id, name").or_(
        "name.ilike.%Siqueira%,name.ilike.%Barbosa%").execute()).data
    for p in pats:
        appts = (await client.from_("appointments").select(
            "start_time, status, booking_fee_paid_at, paid_at"
        ).eq("patient_id", p["id"]).execute()).data
        print(f"  {p['name']!r}: {len(appts)} consulta(s)")
        for a in appts:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"      {st} | {a['status']} | taxa={a['booking_fee_paid_at']} | quitado={a['paid_at']}")

asyncio.run(main())
