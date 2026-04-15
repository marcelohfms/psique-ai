import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = ZoneInfo("America/Recife")

# Column order: Data, Nome completo, Idade, Telefone, E-mail, Tipo de solicitação, Observação
_SHEET_RANGE = "Solicitações!A:G"

# Column order: Data do Pagamento, Paciente, Médico, Data da Consulta, Valor, Telefone, Comprovante, Conferência Humana
_PAYMENTS_SHEET_RANGE = "Pagamentos!A:H"


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=[
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


def _append_row_payments(service, spreadsheet_id: str, row: list) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_PAYMENTS_SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()


async def append_payment_receipt(
    patient_name: str,
    phone: str,
    doctor_name: str,
    appointment_dt: str,
    amount: str,
    drive_link: str,
) -> None:
    """Append a payment receipt row to the Pagamentos sheet.
    Does nothing if GOOGLE_SHEETS_PAYMENTS_ID is not configured.
    Columns: Data do Pagamento | Paciente | Médico | Data da Consulta | Valor | Telefone | Comprovante | Conferência Humana
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    if not spreadsheet_id:
        return

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    phone_clean = phone.replace("@s.whatsapp.net", "")
    row = [now, patient_name, doctor_name, appointment_dt, amount, phone_clean, drive_link, ""]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _append_row_payments, service, spreadsheet_id, row)


async def get_controlled_medications() -> list[str]:
    """Return list of controlled medication names (lowercase) from the 'Receita' tab, column A.
    Returns empty list if sheet is not configured or tab is empty.
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_DOC_ID")
    if not spreadsheet_id:
        return []

    def _read(service) -> list[str]:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Receita!A:A",
        ).execute()
        rows = result.get("values", [])
        return [row[0].strip().lower() for row in rows if row and row[0].strip()]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _read, service)
    except Exception:
        return []


async def append_document_request(
    patient_name: str,
    patient_age: int | None,
    phone: str,
    patient_email: str,
    document_type: str,
    medication_note: str = "",
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
    row = [now, patient_name, age_str, phone_clean, patient_email, document_type, medication_note]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _append_row, service, spreadsheet_id, row)
