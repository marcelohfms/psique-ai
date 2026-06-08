import asyncio
import io
import os
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


def _rename_file(service, file_id: str, new_name: str) -> None:
    service.files().update(fileId=file_id, body={"name": new_name}).execute()


async def rename_file(file_id: str, new_name: str) -> None:
    """Rename an existing Drive file."""
    creds = _credentials()
    service = build("drive", "v3", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _rename_file, service, file_id, new_name)


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
