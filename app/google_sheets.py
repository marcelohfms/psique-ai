import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = ZoneInfo("America/Recife")

# Column order: Data, Nome completo, Idade, Telefone, E-mail, Tipo de solicitação
_SHEET_RANGE = "Solicitações!A:F"


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=[
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/spreadsheets",
        ],
    )


def _append_row(service, spreadsheet_id: str, row: list) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()


async def append_document_request(
    patient_name: str,
    patient_age: int | None,
    phone: str,
    patient_email: str,
    document_type: str,
) -> None:
    """Append a document request row to the Google Sheets spreadsheet.
    Does nothing if GOOGLE_SHEETS_DOC_ID is not configured.
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_DOC_ID")
    if not spreadsheet_id:
        return

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    age_str = str(patient_age) if patient_age else "—"
    phone_clean = phone.replace("@s.whatsapp.net", "")
    row = [now, patient_name, age_str, phone_clean, patient_email, document_type]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _append_row, service, spreadsheet_id, row)
