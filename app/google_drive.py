import asyncio
import io
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

TZ = ZoneInfo("America/Recife")

_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=_SCOPES,
    )


def _upload_and_share(service, folder_id: str, filename: str, image_bytes: bytes, mimetype: str = "image/jpeg") -> str:
    """Upload file bytes to Drive, attempt to make public, return web view link.

    The permission step is best-effort: if it fails (e.g. Workspace admin disabled
    public sharing), the file is still uploaded and a link is still returned.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype=mimetype, resumable=False)
    file = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    file_id = file["id"]
    _logger.info("DRIVE_CREATE OK file_id=%s", file_id)

    try:
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()
    except Exception:
        _logger.warning("DRIVE_SHARE FAILED (file created but not public) file_id=%s", file_id)

    return file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


def build_receipt_filename(patient_name: str, appointment_dt: str, amount: str) -> str:
    """Canonical comprovante (payment receipt) filename, WITHOUT extension.

    Single source of truth for this name: it is both what the Drive file is renamed
    to (see rename_file, which appends the file's real extension) and what the
    Pagamentos sheet displays as the comprovante hyperlink text (see
    app.google_sheets.append_payment_receipt). Rebuilding it separately in each
    place is what made the sheet show a name no file in Drive ever had.

    Format: "{Nome_Do_Paciente}_{DD-MM-AAAA}_R${valor}"
      - amount keeps only digits/separators ("R$ 600,00" → "600,00"), then uses "-"
        instead of ","/"." so the value can't collide with extension parsing.
        Falls back to "valor-nao-identificado" when the amount wasn't identified
        (amount="?" or empty), instead of emitting a broken trailing "_R$".
      - appointment_dt is the linked appointment ("DD/MM/AAAA HH:MM"); today's date
        is used when there is no linked appointment ("—" or empty).
    """
    amount_digits = re.sub(r"[^\d,.]", "", amount or "")
    amount_clean = (
        amount_digits.replace(",", "-").replace(".", "-")
        if amount_digits
        else "valor-nao-identificado"
    )
    date_clean = (
        appointment_dt.split(" ")[0].replace("/", "-")
        if appointment_dt and appointment_dt != "—"
        else datetime.now(TZ).strftime("%d-%m-%Y")
    )
    safe_name = (patient_name or "paciente").replace(" ", "_")
    return f"{safe_name}_{date_clean}_R${amount_clean}"


def _rename_file(service, file_id: str, new_name: str) -> str:
    """Rename a Drive file, preserving its current extension. Returns the final name.

    new_name is passed WITHOUT an extension — callers (e.g. register_payment)
    don't reliably know whether the underlying upload was a jpg or a pdf, so we
    read the file's existing name from Drive and reuse its extension instead of
    guessing/hardcoding one. The resolved name is returned because the extension is
    only known here, and the Pagamentos sheet has to display exactly this name.
    """
    meta = service.files().get(fileId=file_id, fields="name").execute()
    current_name = meta.get("name", "")
    _, dot, ext = current_name.rpartition(".")
    final_name = f"{new_name}.{ext}" if dot else new_name
    service.files().update(fileId=file_id, body={"name": final_name}).execute()
    return final_name


async def rename_file(file_id: str, new_name: str) -> str:
    """Rename an existing Drive file and return its final name. new_name should have
    no extension — the file's current extension is preserved automatically and
    included in the returned name (see _rename_file)."""
    creds = _credentials()
    service = build("drive", "v3", credentials=creds)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _rename_file, service, file_id, new_name)


async def upload_image(image_bytes: bytes, filename: str, mimetype: str = "image/jpeg") -> str:
    """Upload image or PDF bytes to the payments Drive folder. Returns public web view URL."""
    folder_id = os.getenv("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID", "")
    if not folder_id:
        raise ValueError("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID is not set")
    creds = _credentials()
    service = build("drive", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload_and_share, service, folder_id, filename, image_bytes, mimetype)


async def upload_document(file_bytes: bytes, filename: str, mimetype: str = "image/jpeg") -> str:
    """Upload document bytes to the documents Drive folder. Returns public web view URL."""
    folder_id = os.getenv("GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID", "")
    if not folder_id:
        raise ValueError("GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID is not set")
    creds = _credentials()
    service = build("drive", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload_and_share, service, folder_id, filename, file_bytes, mimetype)
