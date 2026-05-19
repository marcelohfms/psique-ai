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
import re
import unicodedata
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


def _safe_name(name: str) -> str:
    """Normalize name for use in a filename (no accents, no special chars)."""
    normalized = unicodedata.normalize("NFD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9_-]", "_", ascii_name).strip("_")


async def _get_patient_name(phone: str) -> str:
    """Look up patient name from DB for use in filenames. Returns 'paciente' on failure."""
    try:
        from app.database import get_user_by_phone
        user = await get_user_by_phone(phone)
        if user:
            name = user.get("patient_name") or user.get("name") or ""
            if name:
                return _safe_name(name.split()[0])
    except Exception:
        pass
    return "paciente"


async def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    """Transcribe raw audio bytes with Whisper."""
    result = await _get_openai().audio.transcriptions.create(
        model="whisper-1",
        file=("audio.ogg", audio_bytes, "audio/ogg"),
        language="pt",
    )
    return f"[áudio transcrito]: {result.text}"


async def transcribe_audio(media_id: str) -> str:
    """Download audio from Meta and transcribe with Whisper."""
    audio_bytes = await download_media(media_id)
    return await transcribe_audio_bytes(audio_bytes)


async def describe_image_bytes(image_bytes: bytes, phone: str = "") -> str | None:
    """Classify image/PDF and route accordingly.

    - Payment receipts (COMPROVANTE DE PAGAMENTO): upload to payments Drive folder and
      return a description string so Eva can call register_payment.
    - Medical documents (exams, laudos, etc.): upload to documents Drive folder with
      filename "{patient}_{date}.jpg", send a thank-you message directly to the patient,
      notify the clinic, and return None (so Eva is never invoked).
    """
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
                        "comece com 'COMPROVANTE DE PAGAMENTO:' e inclua obrigatoriamente: "
                        "valor transferido, chave PIX ou CPF/CNPJ do destinatário (campo 'Chave', 'Para', 'Favorecido' ou similar), "
                        "nome do destinatário se visível, data/hora da transação, "
                        "e qualquer texto adicional visível no comprovante (como 'agendamento', 'taxa', descrição etc.). "
                        "Para qualquer outro tipo de imagem ou documento médico (exame, laudo, receita, resultado, atestado etc.), "
                        "comece com 'DOCUMENTO:' seguido de uma descrição resumida do tipo de documento."
                    ),
                },
            ],
        }],
        max_tokens=300,
    )
    description = resp.choices[0].message.content or ""
    is_payment = description.upper().startswith("COMPROVANTE DE PAGAMENTO")
    now = datetime.now(TZ)
    now_str = now.strftime("%Y%m%d_%H%M%S")
    date_str = now.strftime("%d-%m-%Y")
    patient = await _get_patient_name(phone) if phone else "paciente"

    if is_payment:
        # ── Payment receipt: upload and hand off to Eva's register_payment tool ──
        folder_id = os.getenv("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID")
        if folder_id:
            try:
                from app.google_drive import upload_image
                drive_link = await upload_image(image_bytes, f"comprovante_{patient}_{now_str}.jpg")
                logger.info("DRIVE_UPLOAD OK link=%s", drive_link)
                return f"[imagem]: {description} [drive_link:{drive_link}]"
            except Exception:
                logger.exception("DRIVE_UPLOAD FAILED folder_id=%s", folder_id)
        return f"[imagem]: {description}"

    # ── Medical document: save to Drive, thank patient directly, notify clinic ──
    drive_link = ""
    folder_id = os.getenv("GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID")
    if folder_id:
        try:
            from app.google_drive import upload_document
            filename = f"{patient}_{date_str}.jpg"
            drive_link = await upload_document(image_bytes, filename)
            logger.info("DRIVE_UPLOAD DOCUMENT OK link=%s drive_link=%s", drive_link, drive_link)
        except Exception:
            logger.exception("DRIVE_UPLOAD DOCUMENT FAILED folder_id=%s", folder_id)

    # Send thank-you directly — bypass Eva entirely
    if phone:
        try:
            from app.whatsapp import send_text as _send
            from app.database import save_message as _save_msg
            thank_you = (
                "Recebemos seu documento! 📄\n"
                "Ele será encaminhado ao seu médico em breve. Obrigado! 😊"
            )
            await _send(phone, thank_you)
            await _save_msg(phone, "assistant", thank_you)
        except Exception:
            logger.exception("DOCUMENT_THANKYOU SEND FAILED phone=%s", phone)

        # Notify clinic
        try:
            from app.email_sender import send_clinic_notification_email
            patient_display = patient.replace("_", " ").title()
            phone_clean = phone.replace("@s.whatsapp.net", "")
            notify_msg = (
                f"📄 Documento recebido via WhatsApp\n"
                f"Paciente: {patient_display}\n"
                f"Número: {phone_clean}\n"
                f"Data: {date_str}\n"
                f"Descrição: {description}"
            )
            if drive_link:
                notify_msg += f"\nLink Drive: {drive_link}"
            await send_clinic_notification_email(
                f"Documento recebido — {patient_display}", notify_msg
            )
        except Exception:
            logger.exception("DOCUMENT_CLINIC_NOTIFY FAILED phone=%s", phone)

    # Return None → caller skips Eva processing (document already handled)
    return None


async def describe_pdf_bytes(pdf_bytes: bytes, phone: str = "") -> str:
    """Convert first page of PDF to image and describe with GPT-4o vision."""
    import fitz  # pymupdf
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    image_bytes = pix.tobytes("jpeg")
    doc.close()
    return await describe_image_bytes(image_bytes, phone)


async def describe_image(media_id: str, phone: str = "") -> str:
    """Download image from Meta, classify, upload to Drive, and describe with GPT-4o vision."""
    image_bytes = await download_media(media_id)
    return await describe_image_bytes(image_bytes, phone)


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
            return await describe_image(media_id, phone)
    except Exception:
        logger.exception("Failed to process media %s (type=%s)", media_id, media_type)
    return None
