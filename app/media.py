"""
Download and process media messages from UAZAPI.

Flow:
  1. POST /message/download {messageid} → {url: "..."}
  2. GET url → raw bytes
  3. AudioMessage → OpenAI Whisper transcription
  4. ImageMessage → GPT-4o vision description
"""
import base64
import logging

import httpx
from openai import AsyncOpenAI

from app.uazapi import BASE_URL, _headers

logger = logging.getLogger(__name__)

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
        url = resp.json().get("url") or resp.json().get("mediaUrl")
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
        file=("audio.ogg", audio_bytes, "audio/ogg"),
        language="pt",
    )
    return f"[áudio transcrito]: {result.text}"


async def describe_image(message_id: str) -> str:
    """Download image and describe with GPT-4o vision."""
    image_bytes = await _download(message_id)
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
                    "text": "Descreva o conteúdo desta imagem em português de forma objetiva.",
                },
            ],
        }],
        max_tokens=300,
    )
    return f"[imagem descrita]: {resp.choices[0].message.content}"


async def process_media(message_id: str, media_type: str) -> str | None:
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
