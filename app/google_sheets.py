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

CONTROLLED_MEDICATIONS: list[str] = [
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

# Column order: Data do Pagamento | Paciente | Médico | Data da Consulta | Valor | Telefone | Tipo | Forma de Pagamento | Comprovante | Conferência Humana
_PAYMENTS_SHEET_RANGE = "Pagamentos!A:J"


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


def _append_row_payments(service, spreadsheet_id: str, row: list) -> str:
    """Append a row and return the A1 range that was written (e.g. 'Pagamentos!A5:J5')."""
    response = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_PAYMENTS_SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
    return response.get("updates", {}).get("updatedRange", "")


def _set_hyperlink_cell(service, spreadsheet_id: str, updated_range: str, drive_link: str, filename: str) -> None:
    """Update the comprovante cell (column I) with a clickable hyperlink.

    Uses textFormatRuns + userEnteredValue (stringValue) instead of a HYPERLINK formula.
    This approach is fully locale-independent: no formula syntax, no separator issues
    between pt_BR (semicolons) and en_US (commas) locales.
    The cell displays `filename` as clickable text that opens `drive_link`.
    """
    import re
    # Extract sheet name and row number from the updated_range, e.g. "Pagamentos!A5:J5"
    match = re.search(r"'?([^'!]+)'?!(?:[A-Z]+)(\d+)", updated_range)
    if not match:
        logger.warning("_set_hyperlink_cell: could not parse range %r — skipping hyperlink update", updated_range)
        return

    sheet_name = match.group(1)
    row_number = int(match.group(2))  # 1-based

    # Get sheet ID
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets(properties)").execute()
    sheet_id = next(
        (s["properties"]["sheetId"] for s in meta.get("sheets", [])
         if s["properties"]["title"] == sheet_name),
        None,
    )
    if sheet_id is None:
        logger.warning("_set_hyperlink_cell: sheet %r not found", sheet_name)
        return

    # Column I = index 8 (0-based)
    col_index = 8
    safe_link = drive_link.strip()

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{
                "updateCells": {
                    "rows": [{
                        "values": [{
                            "userEnteredValue": {"stringValue": filename},
                            "textFormatRuns": [{
                                "startIndex": 0,
                                "format": {"link": {"uri": safe_link}},
                            }],
                        }]
                    }],
                    "fields": "userEnteredValue,textFormatRuns",
                    "start": {
                        "sheetId": sheet_id,
                        "rowIndex": row_number - 1,  # 0-based
                        "columnIndex": col_index,
                    },
                }
            }]
        },
    ).execute()


async def append_payment_receipt(
    patient_name: str,
    phone: str,
    doctor_name: str,
    appointment_dt: str,
    amount: str,
    drive_link: str,
    payment_type: str = "",
    payment_method_override: str = "",
) -> None:
    """Append a payment receipt row to the Pagamentos sheet.
    Does nothing if GOOGLE_SHEETS_PAYMENTS_ID is not configured.
    Columns: Data do Pagamento | Paciente | Médico | Data da Consulta | Valor | Telefone | Tipo | Forma de Pagamento | Comprovante | Conferência Humana
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    if not spreadsheet_id:
        logger.error("GOOGLE_SHEETS_PAYMENTS_ID not set — payment receipt NOT recorded in spreadsheet")
        return

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    phone_clean = phone.replace("@s.whatsapp.net", "")

    # Determine payment method for the Forma de Pagamento column
    if payment_method_override:
        payment_method = payment_method_override
    else:
        payment_method = "Link" if "link" in payment_type.lower() else "PIX"

    # Build filename for the comprovante hyperlink
    comprovante_formula_args: tuple[str, str] | None = None
    if drive_link:
        drive_link = drive_link.strip()
        safe_name = patient_name.replace(" ", "_")
        amount_clean = amount.replace("R$", "").replace(" ", "").strip()
        date_clean = appointment_dt.split(" ")[0].replace("/", "-") if appointment_dt != "—" else datetime.now(TZ).strftime("%d-%m-%Y")
        filename = f"{safe_name}_{date_clean}_R${amount_clean}.jpg"
        comprovante_formula_args = (drive_link, filename)

    # Write the row with an empty comprovante column (column I).
    # The HYPERLINK formula is applied in a separate batchUpdate call below,
    # which uses formulaValue (locale-independent) to avoid the USER_ENTERED
    # formula parsing issue that caused the comprovante to shift into column H.
    row = [now, patient_name, doctor_name, appointment_dt, amount, phone_clean, payment_type, payment_method, "", ""]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_running_loop()
    updated_range = await loop.run_in_executor(None, _append_row_payments, service, spreadsheet_id, row)
    logger.info("append_payment_receipt: row written at range=%r patient=%s", updated_range, patient_name)

    # Now set the HYPERLINK formula in column I of the newly appended row.
    # Isolated in its own try/except: a hyperlink failure must NOT roll back the row write.
    if comprovante_formula_args and updated_range:
        try:
            await loop.run_in_executor(
                None, _set_hyperlink_cell, service, spreadsheet_id,
                updated_range, comprovante_formula_args[0], comprovante_formula_args[1],
            )
            logger.info("append_payment_receipt: hyperlink set at range=%r", updated_range)
        except Exception:
            logger.exception(
                "append_payment_receipt: hyperlink FAILED (row was written) "
                "range=%r drive_link=%r filename=%r",
                updated_range, comprovante_formula_args[0], comprovante_formula_args[1],
            )


async def append_refund_request(
    patient_name: str,
    phone: str,
    doctor_name: str,
    appointment_dt: str,
    amount: str,
    reason: str = "",
) -> None:
    """Append a refund request row to the Pagamentos sheet.
    Columns: Data do Pagamento | Paciente | Médico | Data da Consulta | Valor | Telefone | Tipo | Forma de Pagamento | Comprovante | Conferência Humana
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    if not spreadsheet_id:
        logger.error("GOOGLE_SHEETS_PAYMENTS_ID not set — refund request NOT recorded in spreadsheet")
        return

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    phone_clean = phone.replace("@s.whatsapp.net", "")
    obs = f"Motivo: {reason}" if reason else ""

    amount_clean = amount.strip().lstrip("-")
    row = [now, patient_name, doctor_name, appointment_dt, f"-{amount_clean}", phone_clean, "Reembolso - Taxa de Reserva", "—", obs, ""]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_row_payments, service, spreadsheet_id, row)


async def append_document_request(
    patient_name: str,
    patient_age: int | None,
    phone: str,
    patient_email: str,
    document_type: str,
    medication_note: str = "",
    doctor_name: str = "",
    patient_cpf: str = "",
    financial_name: str = "",
    financial_cpf: str = "",
    financial_email: str = "",
) -> None:
    """Append a document request row to the Google Sheets spreadsheet.
    Does nothing if GOOGLE_SHEETS_DOC_ID is not configured.
    Columns: Data | Nome | Idade | Telefone | E-mail | Tipo | Observação | Médico | CPF | Status
    Para nota_fiscal, substitui nome/cpf/e-mail pelos dados do responsável financeiro se informados.
    """
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_DOC_ID")
    if not spreadsheet_id:
        logger.error("GOOGLE_SHEETS_DOC_ID not set — document request NOT recorded in spreadsheet")
        return

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    age_str = str(patient_age) if patient_age else "—"
    phone_clean = phone.replace("@s.whatsapp.net", "")

    # Para nota fiscal, usa dados financeiros no lugar dos dados do paciente
    if document_type == "nota_fiscal":
        name_row = financial_name or patient_name
        cpf_row = financial_cpf or patient_cpf
        email_row = financial_email or patient_email
    else:
        name_row = patient_name
        cpf_row = patient_cpf
        email_row = patient_email

    row = [now, name_row, age_str, phone_clean, email_row, document_type, medication_note, doctor_name, cpf_row, ""]

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_row, service, spreadsheet_id, row)
