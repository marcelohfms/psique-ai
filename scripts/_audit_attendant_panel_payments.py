import asyncio
from dotenv import load_dotenv
load_dotenv()


async def main():
    import sys
    sys.path.insert(0, "dashboard")
    from db_client import get_client
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import os
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    TZ = ZoneInfo("America/Recife")
    client = await get_client()

    events = await (
        client.from_("events")
        .select("created_at, phone, metadata")
        .eq("event_type", "attendant_pagamento_registrado")
        .order("created_at")
        .execute()
    )
    print(f"total attendant_pagamento_registrado events: {len(events.data or [])}")

    # Pull the payments sheet once
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    spreadsheet_id = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    service = build("sheets", "v4", credentials=creds)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="Pagamentos!A:J"
    ).execute()
    rows = result.get("values", [])

    from collections import defaultdict
    sheet_index = defaultdict(list)
    for row in rows[1:] if rows else []:
        if len(row) < 7:
            continue
        patient_name = row[1].strip()
        appt_dt = row[3]
        payment_type = row[6]
        amount = row[4] if len(row) > 4 else ""
        logged_at = row[0] if row else ""
        date_only = appt_dt.split(" ")[0] if appt_dt else ""
        sheet_index[(patient_name, date_only)].append((payment_type, amount, logged_at, row))

    print(f"\n=== checando cada evento contra a planilha ===")
    problems = []
    for e in events.data or []:
        meta = e.get("metadata") or {}
        appt_ids = (meta.get("appointment_id") or "").split(",")
        tipo = meta.get("tipo")
        valor = meta.get("valor")
        first_aid = appt_ids[0] if appt_ids else None
        if not first_aid:
            continue
        appt = await (
            client.from_("appointments")
            .select("appointment_id, start_time, patients(name)")
            .eq("appointment_id", first_aid)
            .maybe_single()
            .execute()
        )
        if not appt or not appt.data:
            print(f"  [!] evento aponta p/ appointment_id inexistente: {first_aid} ({e['created_at']})")
            continue
        patient_name = (appt.data.get("patients") or {}).get("name", "")
        try:
            dt = datetime.fromisoformat(appt.data["start_time"].replace("Z", "+00:00")).astimezone(TZ)
            date_only = dt.strftime("%d/%m/%Y")
        except Exception:
            date_only = ""

        key = (patient_name.strip(), date_only)
        candidates = sheet_index.get(key, [])

        try:
            event_dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ)
        except Exception:
            event_dt = None

        found = False
        for ptype, amount, logged_at, row in candidates:
            type_ok = ("Taxa" in ptype) if tipo == "taxa" else (ptype in ("Consulta", "Pagamento Parcial"))
            if not type_ok:
                continue
            amount_ok = str(amount).strip() == str(valor).strip()
            # logged_at (col A, "DD/MM/YYYY HH:MM") should be close (same day) to the event timestamp
            time_ok = False
            if event_dt and logged_at:
                try:
                    logged_dt = datetime.strptime(logged_at, "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
                    time_ok = abs((logged_dt - event_dt).total_seconds()) < 3600
                except Exception:
                    pass
            if amount_ok and time_ok:
                found = True
                break

        status = "OK" if found else "*** FALTANDO NA PLANILHA ***"
        print(f"  {e['created_at']} | {patient_name} | {date_only} | tipo={tipo} valor={valor} | appt={first_aid} | {status}")
        if not found:
            problems.append((e["created_at"], patient_name, date_only, tipo, valor, first_aid, e.get("phone")))

    print(f"\n=== RESUMO: {len(problems)} pagamento(s) do painel sem linha na planilha ===")
    for p in problems:
        print(p)

asyncio.run(main())
