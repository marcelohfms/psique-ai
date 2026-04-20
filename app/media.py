"""
Download and process media messages from WhatsApp Cloud API (Meta).

Flow:
  1. Receive media_id from webhook payload
  2. Resolve media URL via GET graph.facebook.com/v19.0/{media_id}
  3. Download bytes using Authorization header
  4. AudioMessage → OpenAI Whisper transcription
  5. ImageMessage → upload to Drive (if configured) + GPT-4o vision description
"""
import base64
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from app.whatsapp import download_media

logger = logging.getLogger(__name__)
TZ = ZoneInfo("America/Recife")

_openai: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI()
    return _openai


async def transcribe_audio(media_id: str) -> str:
    """Download audio and transcribe with Whisper."""
    audio_bytes = await download_media(media_id)
    result = await _get_openai().audio.transcriptions.create(
        model="whisper-1",
        file=("audio.ogg", audio_bytes, "audio/ogg"),
        language="pt",
    )
    return f"[áudio transcrito]: {result.text}"


async def describe_image(media_id: str) -> str:
    """Download image, classify, upload to Drive, and describe with GPT-4o vision."""
    image_bytes = await download_media(media_id)

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


async def process_media(media_id: str, media_type: str, phone: str = "") -> str | None:
    """
    Returns transcribed/described text for audio or image messages.
    media_type: 'audio' or 'image' (Meta Cloud API types).
    Returns None for unsupported types.
    """
    try:
        if media_type == "audio":
            return await transcribe_audio(media_id)
        if media_type == "image":
            return await describe_image(media_id)
    except Exception:
        logger.exception("Failed to process media %s (type=%s)", media_id, media_type)
    return None
