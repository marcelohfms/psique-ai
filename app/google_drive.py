import asyncio
import io
import os

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.uazapi import BASE_URL, _headers

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


async def _download_from_uazapi(message_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/message/download",
            json={"id": message_id},
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("fileURL") or data.get("url") or data.get("mediaUrl")
        if not url:
            raise ValueError(f"No URL in download response: {resp.text}")
        media = await client.get(url, follow_redirects=True)
        media.raise_for_status()
        return media.content


def _upload_and_share(service, folder_id: str, filename: str, image_bytes: bytes) -> str:
    """Upload image to Drive, make it public, and return the web view link."""
    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype="image/jpeg", resumable=False)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,webViewLink",
    ).execute()

    file_id = file["id"]
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    return file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


async def upload_comprovante(message_id: str, filename: str) -> str:
    """Download image from UAZAPI, upload to Drive folder, and return public web view URL."""
    folder_id = os.getenv("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID", "")
    if not folder_id:
        raise ValueError("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID is not set")

    image_bytes = await _download_from_uazapi(message_id)
    creds = _credentials()
    service = build("drive", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload_and_share, service, folder_id, filename, image_bytes)
