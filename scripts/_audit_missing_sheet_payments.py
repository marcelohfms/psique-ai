import asyncio
from dotenv import load_dotenv
load_dotenv()


async def main():
    import sys
    sys.path.insert(0, "dashboard")
    from db_client import get_client
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    import os
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    TZ = ZoneInfo("America/Recife")
    client = await get_client()

    since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    appts = await (
        client.from_("appointments")
        .select(
            "appointment_id, patient_id, start_time, doctor_id, paid_at, "
            "booking_fee_paid_at, booking_fee_waived, status, "
            "patients(name)"
        )
        .gte("start_time", since)
        .or_("paid_at.not.is.null,booking_fee_paid_at.not.is.null")
        .order("start_time")
        .execute()
    )
    print(f"appointments com algum pagamento nos últimos 60 dias: {len(appts.data or [])}")

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

    # Index sheet rows by (patient_name, appointment_date_str) -> list of payment_type
    from collections import defaultdict
    sheet_index = defaultdict(list)
    for row in rows[1:] if rows else []:
        if len(row) < 7:
            continue
        patient_name = row[1]
        appt_dt = row[3]
        payment_type = row[6]
        date_only = appt_dt.split(" ")[0] if appt_dt else ""
        sheet_index[(patient_name.strip(), date_only)].append(payment_type)

    missing = []
    for a in appts.data or []:
        patient = a.get("patients") or {}
        name = (patient.get("name") or "").strip()
        try:
            dt = datetime.fromisoformat(a["start_time"].replace("Z", "+00:00")).astimezone(TZ)
            date_only = dt.strftime("%d/%m/%Y")
        except Exception:
            date_only = ""
        key = (name, date_only)
        types_present = sheet_index.get(key, [])

        if a.get("booking_fee_paid_at") and not a.get("booking_fee_waived"):
            if not any("Taxa" in t for t in types_present):
                missing.append((name, date_only, "TAXA", a["appointment_id"], a["booking_fee_paid_at"]))

        if a.get("paid_at"):
            if not any(t in ("Consulta", "Pagamento Parcial") for t in types_present):
                missing.append((name, date_only, "CONSULTA", a["appointment_id"], a["paid_at"]))

    print(f"\n=== possíveis pagamentos SEM linha correspondente na planilha ({len(missing)}) ===")
    for m in missing:
        print(m)

asyncio.run(main())
