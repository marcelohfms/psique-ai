"""
Download and process media messages from WhatsApp Cloud API (Meta).

Flow:
  1. Receive media_id from webhook payload
  2. Resolve media URL via GET graph.facebook.com/v19.0/{media_id}
  3. Download bytes using Authorization header
  4. ImageMessage → upload to Drive (if configured) + vision classification/description
     (áudio nunca é transcrito — o paciente recebe aviso de que áudio não é processado)
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
    """Look up patient's full name from DB for use in filenames. Returns 'paciente' on failure."""
    try:
        from app.database import get_user_by_phone
        user = await get_user_by_phone(phone)
        if user:
            name = user.get("patient_name") or user.get("name") or ""
            if name:
                return _safe_name(" ".join(name.split()))
    except Exception:
        pass
    return "paciente"


_DOC_TYPE_PREFIXES: list[tuple[str, str]] = [
    ("COMPROVANTE DE PAGAMENTO", "comprovante"),
    ("EXAME", "exame"),
    ("LAUDO", "laudo"),
    ("ATESTADO", "atestado"),
    ("RECEITA", "receita"),
    ("DECLARACAO", "declaracao"),
    ("DECLARAÇÃO", "declaracao"),
    ("RELATORIO", "relatorio"),
    ("RELATÓRIO", "relatorio"),
    ("DOCUMENTO", "documento"),
]


RECEIPT_PREFIX = "COMPROVANTE DE PAGAMENTO"
IMAGE_TAG = "[imagem]:"


def is_payment_receipt_message(content: str) -> bool:
    """True when `content` carries a media description that the vision classifier
    tagged as a payment receipt — i.e. what describe_image_bytes() emits for
    CATEGORIA 1 ("[imagem]: COMPROVANTE DE PAGAMENTO: ...").

    Deliberately strict: it is NOT "any image", and it is NOT "the words
    comprovante de pagamento appear somewhere". A photo, sticker or bom-dia image
    is classified as IGNORAR and never reaches Eva; a medical document gets its
    own prefix (EXAME:, LAUDO:, ...). Only the classifier's own receipt prefix,
    written immediately after an [imagem]: tag, counts here.

    The tag is searched for ANYWHERE in the message, not only at the start: what
    reaches the graph is rarely the bare classifier output. A caption is prepended
    by both webhooks ("Pagamento ok\\n[imagem]: COMPROVANTE DE PAGAMENTO: ...",
    app/main.py) and the debounce buffer joins everything the patient sent in the
    window into one string (app/buffer.py), so a receipt can sit after a caption,
    after another image, or before a follow-up text. Anchoring on startswith()
    made all of those invisible to _route_entry — the exact failure this
    detector exists to prevent (4 casos reais em `messages` até 31/07/2026).
    """
    if not content:
        return False
    text = str(content)
    idx = text.find(IMAGE_TAG)
    while idx != -1:
        description = text[idx + len(IMAGE_TAG):].lstrip()
        if description.upper().startswith(RECEIPT_PREFIX):
            return True
        idx = text.find(IMAGE_TAG, idx + 1)
    return False


def _extract_doc_type(description: str) -> str:
    upper = description.upper()
    for prefix, slug in _DOC_TYPE_PREFIXES:
        if upper.startswith(prefix + ":") or upper.startswith(prefix + " "):
            return slug
    return "documento"


_DECORATION_CHARS = "*_#>`'\"“”‘’ \t\r\n"
_CATEGORY_LABEL_RE = re.compile(r"(?i)^categoria\s*\d+\s*[—–:\-]*\s*")

# Frases que só aparecem quando o conteúdo da imagem é um comprovante — cobrem o
# vocabulário do próprio prompt (CATEGORIA 1) e o texto típico de um Pix. Usadas
# para resgatar um comprovante que o modelo descreveu sob o prefixo errado.
_RECEIPT_RESCUE_MARKERS = (
    "COMPROVANTE DE PAGAMENTO",
    "COMPROVANTE DE TRANSFER",  # transferência / transferencia
    "COMPROVANTE PIX",
    "COMPROVANTE DO PIX",
    "COMPROVANTE DE PIX",
    "TRANSFERÊNCIA PIX",
    "TRANSFERENCIA PIX",
    "RECIBO DE PAGAMENTO",
)


def _strip_decoration(text: str) -> str:
    """Remove enfeite cosmético do início da resposta do classificador: markdown
    (**, `, #), aspas, espaço e um eventual rótulo "CATEGORIA n —"."""
    cleaned = (text or "").strip().lstrip(_DECORATION_CHARS)
    cleaned = _CATEGORY_LABEL_RE.sub("", cleaned)
    return cleaned.lstrip(_DECORATION_CHARS)


def _has_known_doc_prefix(description: str) -> bool:
    upper = description.upper()
    return any(
        upper.startswith(prefix + ":") or upper.startswith(prefix + " ")
        for prefix, _slug in _DOC_TYPE_PREFIXES
    )


def classify_media_description(description: str) -> tuple[str, str]:
    """Interpreta a resposta crua do classificador de visão.

    Devolve (kind, canonical): kind ∈ {"payment", "document", "ignore"}; canonical
    é o texto a usar dali em diante. Para "payment", canonical SEMPRE começa com
    "COMPROVANTE DE PAGAMENTO" — é isso que is_payment_receipt_message() e o
    _route_entry reconhecem depois.

    O caso Igor Lapsky (27/08/2026) mostrou que ancorar a decisão num
    startswith() da resposta crua é frágil: um comprovante Pix legível caiu no
    ramo de documento porque a resposta do modelo não começava exatamente com o
    prefixo. Aqui a decoração é tolerada e um comprovante descrito sob prefixo de
    documento (ou sem prefixo nenhum) é resgatado pelo conteúdo.
    """
    cleaned = _strip_decoration(description)
    upper = cleaned.upper()

    if upper.startswith("IGNORAR"):
        return "ignore", cleaned
    if upper.startswith(RECEIPT_PREFIX):
        return "payment", cleaned

    if any(marker in upper for marker in _RECEIPT_RESCUE_MARKERS):
        body = cleaned
        for prefix, _slug in _DOC_TYPE_PREFIXES:
            if upper.startswith(prefix + ":") or upper.startswith(prefix + " "):
                body = cleaned[len(prefix) + 1:].strip()
                break
        return "payment", f"{RECEIPT_PREFIX}: {body}"

    return "document", cleaned


async def describe_image_bytes(
    image_bytes: bytes,
    phone: str = "",
    source_bytes: bytes | None = None,
    source_ext: str = "jpg",
) -> str | None:
    """Classify image/PDF and route accordingly.

    - Payment receipts (COMPROVANTE DE PAGAMENTO): upload to payments Drive folder and
      return a description string so Eva can call register_payment.
    - Medical documents (exams, laudos, etc.): upload to documents Drive folder with
      filename "{doc_type}_{patient}_{date}.{ext}", send a thank-you message directly
      to the patient, notify the clinic, and return None (so Eva is never invoked).

    source_bytes: original file bytes to upload (e.g. raw PDF). If None, image_bytes is used.
    source_ext: file extension for the uploaded file ('jpg' or 'pdf').
    """
    b64 = base64.b64encode(image_bytes).decode()
    # gpt-5.2: temperature=0 deixa a classificação determinística; reasoning_effort="none"
    # (classificação não precisa de raciocínio) e max_completion_tokens (a família gpt-5 rejeita max_tokens)
    resp = await _get_openai().chat.completions.create(
        model="gpt-5.2",
        temperature=0,
        reasoning_effort="none",
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
                        "Classifique esta imagem em UMA categoria e responda EXATAMENTE com o prefixo correspondente:\n\n"
                        "CATEGORIA 1 — comprovante de pagamento (PIX, TED, DOC, transferência bancária, recibo de pagamento, "
                        "extrato de transação bancária):\n"
                        "  Responda começando com exatamente: 'COMPROVANTE DE PAGAMENTO:'\n"
                        "  Em seguida inclua: valor transferido, chave PIX ou CPF/CNPJ do destinatário, "
                        "nome do destinatário se visível, data/hora da transação, e qualquer texto adicional "
                        "visível (como 'agendamento', 'taxa', descrição etc.).\n\n"
                        "CATEGORIA 2 — documento médico ou pessoal. Identifique o tipo e use o prefixo exato:\n"
                        "  'EXAME:' — exame laboratorial, de imagem, eletrocardiograma, etc.\n"
                        "  'LAUDO:' — laudo médico, psicológico ou psiquiátrico\n"
                        "  'ATESTADO:' — atestado médico\n"
                        "  'RECEITA:' — receita ou prescrição médica\n"
                        "  'DECLARACAO:' — declaração de comparecimento, internação, etc.\n"
                        "  'RELATORIO:' — relatório médico\n"
                        "  'DOCUMENTO:' — qualquer outro tipo de documento\n"
                        "  Em seguida inclua uma descrição resumida do conteúdo.\n\n"
                        "CATEGORIA 3 — imagem irrelevante para uma clínica médica:\n"
                        "  Inclui: mensagens de bom dia/boa tarde/boa noite, imagens motivacionais, "
                        "religiosas, de gratidão, memes, fotos pessoais, figurinhas, paisagens, "
                        "correntes, frases inspiracionais ou qualquer imagem sem conteúdo médico ou financeiro.\n"
                        "  Responda APENAS com: 'IGNORAR'\n\n"
                        "IMPORTANTE: use APENAS esses prefixos. Não invente outros."
                    ),
                },
            ],
        }],
        max_completion_tokens=300,
    )
    raw_description = resp.choices[0].message.content or ""
    # A resposta crua fica sempre no log: no caso Igor (27/08/2026) ela só existia
    # no e-mail da clínica e a rota errada ficou impossível de auditar depois.
    logger.info("IMAGE_CLASSIFIER_RAW phone=%s output=%r", phone, raw_description[:300])

    kind, description = classify_media_description(raw_description)

    # Imagens irrelevantes (bom dia, motivacionais, etc.) — descartar silenciosamente
    if kind == "ignore":
        logger.info("IMAGE_IGNORED phone=%s (bom dia / imagem irrelevante)", phone)
        return None

    is_payment = kind == "payment"
    if is_payment and not _strip_decoration(raw_description).upper().startswith(RECEIPT_PREFIX):
        logger.warning(
            "IMAGE_CLASSIFIER_RESCUED_AS_PAYMENT phone=%s output=%r",
            phone, raw_description[:300])
    if not is_payment and not _has_known_doc_prefix(description):
        logger.warning(
            "IMAGE_CLASSIFIER_UNRECOGNIZED phone=%s output=%r — caindo no fluxo de documento",
            phone, raw_description[:300])
    doc_type = _extract_doc_type(description)
    now = datetime.now(TZ)
    now_str = now.strftime("%Y%m%d_%H%M%S")
    date_str = now.strftime("%d-%m-%Y")
    patient = await _get_patient_name(phone) if phone else "paciente"

    if is_payment:
        # ── Payment receipt: upload and hand off to Eva's register_payment tool ──
        folder_id = os.getenv("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID")
        if not folder_id:
            logger.warning("GOOGLE_DRIVE_PAYMENTS_FOLDER_ID not set — skipping Drive upload for comprovante")
        else:
            try:
                upload_bytes = source_bytes if source_bytes is not None else image_bytes
                ext = source_ext  # "pdf" or "jpg"
                comprovante_filename = f"comprovante_{patient}_{now_str}.{ext}"
                from app.google_drive import upload_image
                mimetype = "application/pdf" if ext == "pdf" else "image/jpeg"
                drive_link = await upload_image(upload_bytes, comprovante_filename, mimetype)
                logger.info("DRIVE_UPLOAD OK filename=%s link=%s", comprovante_filename, drive_link)
                return f"[imagem]: {description} [drive_link:{drive_link}]"
            except Exception:
                logger.exception("DRIVE_UPLOAD FAILED folder_id=%s — comprovante enviado para Eva sem link", folder_id)
        return f"[imagem]: {description}"

    # ── Medical document: save to Drive, thank patient directly, notify clinic ──
    upload_bytes = source_bytes if source_bytes is not None else image_bytes
    mimetype = "application/pdf" if source_ext == "pdf" else "image/jpeg"
    drive_link = ""
    folder_id = os.getenv("GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID")
    if folder_id:
        try:
            from app.google_drive import upload_document
            filename = f"{doc_type}_{patient}_{date_str}.{source_ext}"
            drive_link = await upload_document(upload_bytes, filename, mimetype)
            logger.info("DRIVE_UPLOAD DOCUMENT OK filename=%s link=%s", filename, drive_link)
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
                f"📄 Documento recebido via WhatsApp — ANEXAR AO SISTEMA\n\n"
                f"Paciente: {patient_display}\n"
                f"Número: {phone_clean}\n"
                f"Data: {date_str}\n"
                f"Descrição: {description}"
            )
            if drive_link:
                notify_msg += f"\nLink Drive: {drive_link}"
            notify_msg += "\n\n⚠️ Por favor, anexe este documento ao prontuário do paciente no sistema."
            await send_clinic_notification_email(
                f"Documento recebido — {patient_display}", notify_msg
            )
        except Exception:
            logger.exception("DOCUMENT_CLINIC_NOTIFY FAILED phone=%s", phone)

    # Return None → caller skips Eva processing (document already handled)
    return None


async def describe_pdf_bytes(pdf_bytes: bytes, phone: str = "") -> str:
    """Convert first page of PDF to image for classification, but upload the original PDF."""
    import fitz  # pymupdf
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    image_bytes = pix.tobytes("jpeg")
    doc.close()
    return await describe_image_bytes(
        image_bytes,
        phone,
        source_bytes=pdf_bytes,
        source_ext="pdf",
    )


async def describe_image(media_id: str, phone: str = "") -> str:
    """Download image from Meta, classify, upload to Drive, and describe with GPT-4o vision."""
    image_bytes = await download_media(media_id)
    return await describe_image_bytes(image_bytes, phone)


async def process_media(media_id: str, media_type: str, phone: str = "") -> str | None:
    """
    Returns described text for image messages.
    media_type: Meta Cloud API type. Only 'image' is supported — audio is never
    transcribed (patients get an auto-reply that audio isn't processed).
    Returns None for unsupported types.
    """
    try:
        if media_type == "image":
            return await describe_image(media_id, phone)
    except Exception:
        logger.exception("Failed to process media %s (type=%s)", media_id, media_type)
    return None
