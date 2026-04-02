"""
Download and process media messages from UAZAPI.

Flow:
  1. POST /message/download {messageid} → {url: "..."}
  2. GET url → raw bytes
  3. AudioMessage → OpenAI Whisper transcription
  4. ImageMessage → upload to Drive (if configured) + GPT-4o vision description
"""
import base64
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from openai import AsyncOpenAI

from app.uazapi import BASE_URL, _headers

logger = logging.getLogger(__name__)
TZ = ZoneInfo("America/Recife")

_openai: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI()
    return _openai


async def _download(message_id: str) -> bytes:
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


async def transcribe_audio(message_id: str) -> str:
    """Download audio and transcribe with Whisper."""
    audio_bytes = await _download(message_id)
    result = await _get_openai().audio.transcriptions.create(
        model="whisper-1",
        file=("audio.mp3", audio_bytes, "audio/mpeg"),
        language="pt",
    )
    return f"[áudio transcrito]: {result.text}"


async def describe_image(message_id: str) -> str:
    """Download image, classify, upload to Drive, and describe with GPT-4o vision."""
    image_bytes = await _download(message_id)

    # Classify and describe in one vision call
    b64 = base64.b64encode(image_bytes).decode()
    resp = await _get_openai().chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
                {
                    "type": "text",
                    "text": (
                        "Descreva o conteúdo desta imagem em português de forma objetiva. "
                        "Se for um comprovante de pagamento (PIX, TED, DOC, transferência bancária ou recibo de pagamento), "
                        "comece com 'COMPROVANTE DE PAGAMENTO:'. "
                        "Para qualquer outro tipo de imagem ou documento, comece com 'DOCUMENTO:'."
                    ),
                },
            ],
        }],
        max_tokens=300,
    )
    description = resp.choices[0].message.content
    is_payment = description.upper().startswith("COMPROVANTE DE PAGAMENTO")
    now = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")

    if is_payment:
        folder_id = os.getenv("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID")
        if folder_id:
            try:
                from app.google_drive import upload_image
                drive_link = await upload_image(image_bytes, f"comprovante_{now}.jpg")
                logger.info("DRIVE_UPLOAD OK link=%s", drive_link)
                return f"[imagem]: {description} [drive_link:{drive_link}]"
            except Exception:
                logger.exception("DRIVE_UPLOAD FAILED folder_id=%s", folder_id)
        return f"[imagem]: {description}"
    else:
        folder_id = os.getenv("GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID")
        if folder_id:
            try:
                from app.google_drive import upload_document
                drive_link = await upload_document(image_bytes, f"documento_{now}.jpg")
                logger.info("DRIVE_UPLOAD DOCUMENT OK link=%s", drive_link)
                return f"[imagem]: {description} [documento_link:{drive_link}]"
            except Exception:
                logger.exception("DRIVE_UPLOAD DOCUMENT FAILED folder_id=%s", folder_id)
        return f"[imagem]: {description}"


async def process_media(message_id: str, media_type: str, phone: str = "") -> str | None:
    """
    Returns transcribed/described text for audio or image messages.
    Returns None for unsupported types.
    """
    try:
        if media_type == "AudioMessage":
            return await transcribe_audio(message_id)
        if media_type == "ImageMessage":
            return await describe_image(message_id)
    except Exception:
        logger.exception("Failed to process media %s (type=%s)", message_id, media_type)
    return None
