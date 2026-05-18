import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = ZoneInfo("America/Recife")

# Column order: Data, Nome completo, Idade, Telefone, E-mail, Tipo de solicitação, Observação
_SHEET_RANGE = "Solicitações!A:G"

# Controlled medications that always require a physical prescription (receita especial/azul/amarela).
# Used as fallback when the "Receita" sheet tab is not configured or empty.
# Brand names and generic names are both listed (lowercase).
_CONTROLLED_FALLBACK: list[str] = [
    # Benzodiazepínicos
    "rivotril", "clonazepam",
    "diazepam", "valium",
    "alprazolam", "xanax", "frontal",
    "lorazepam", "lorax",
    "bromazepam", "lexotan",
    "midazolam", "dormicum",
    "nitrazepam", "mogadon",
    "flurazepam", "dalmadorm",
    "clobazam", "frisium",
    "zolpidem", "stilnox",
    # Estimulantes (TDAH)
    "metilfenidato", "ritalina", "ritalin", "concerta", "rubifen",
    "lisdexanfetamina", "venvanse", "vyvanse",
    "atomoxetina", "strattera",
    "dexanfetamina", "dextroamphetamine",
]

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

    # Show a clickable hyperlink with the filename instead of the raw URL
    if drive_link:
        safe_name = patient_name.replace(" ", "_")
        amount_clean = amount.replace("R$", "").replace(" ", "").strip()
        date_clean = appointment_dt.split(" ")[0].replace("/", "-") if appointment_dt != "—" else datetime.now(TZ).strftime("%d-%m-%Y")
        filename = f"{safe_name}_{date_clean}_R${amount_clean}.jpg"
        comprovante_cell = f'=HYPERLINK("{drive_link}","{filename}")'
    else:
        comprovante_cell = ""

    row = [now, patient_name, doctor_name, appointment_dt, amount, phone_clean, comprovante_cell, ""]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_row_payments, service, spreadsheet_id, row)


async def get_controlled_medications() -> list[str]:
    """Return list of controlled medication names (lowercase).
    Merges the hardcoded fallback list with any entries in the 'Receita' sheet tab (column A).
    The sheet can be used to extend or add clinic-specific medications.
    """
    sheet_meds: list[str] = []
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_DOC_ID")
    if spreadsheet_id:
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
            sheet_meds = await loop.run_in_executor(None, _read, service)
        except Exception:
            pass

    return list(set(_CONTROLLED_FALLBACK + sheet_meds))


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
        logger.error("GOOGLE_SHEETS_DOC_ID not set — document request NOT recorded in spreadsheet")
        return

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    age_str = str(patient_age) if patient_age else "—"
    phone_clean = phone.replace("@s.whatsapp.net", "")
    row = [now, patient_name, age_str, phone_clean, patient_email, document_type, medication_note]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_row, service, spreadsheet_id, row)
