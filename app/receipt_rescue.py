"""Leitura do comprovante na chegada.

O Chatwoot recebe a mídia por um canal confiável, mas o arquivo pode demorar alguns
segundos para ficar disponível para download. Em vez de esperar a varredura de 10-15 min
(`receipt_reconciliation`), este módulo insiste no download por cerca de 1 minuto e, se
conseguir, reinjeta a imagem no fluxo normal da Eva (respeitando a pausa).
"""
import asyncio
import logging
import time

import httpx

from app.database import log_event
from app.media import describe_image_bytes
from app.phone import _phone_variants
from app.receipt_reconciliation import (
    REPLAY_EVENT,
    already_replayed,
    has_payment_trace_until_now,
    has_trace,
)

logger = logging.getLogger(__name__)

# Espera (s) antes de cada tentativa de download; soma 60s no pior caso.
RESCUE_DOWNLOAD_DELAYS: tuple[int, ...] = (3, 7, 12, 18, 20)
RESCUED_EVENT = "receipt_rescued_on_arrival"


async def _download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as hc:
        resp = await hc.get(url)
        resp.raise_for_status()
        return resp.content


async def rescue_incoming_attachment(
    *,
    phone: str,
    conversation_id: int,
    attachment_id: int,
    data_url: str,
    created_at: float,
    sleep=asyncio.sleep,
) -> bool:
    """Baixa o anexo insistindo e o reinjeta na Eva. True se reinjetou."""
    # Import local: app.main importa este módulo (evita ciclo).
    from app.main import process_message, save_message

    if await already_replayed(attachment_id) or await has_trace(phone, created_at):
        return False

    started = time.monotonic()
    img: bytes | None = None
    for delay in RESCUE_DOWNLOAD_DELAYS:
        await sleep(delay)
        try:
            img = await _download_bytes(data_url)
            break
        except Exception as e:
            logger.info("RESCUE_DOWNLOAD_RETRY attachment_id=%s err=%s", attachment_id, e)
    if not img:
        logger.warning("RESCUE_GAVE_UP attachment_id=%s", attachment_id)
        return False

    # A Meta pode ter ingerido a mesma imagem enquanto esperávamos o download.
    if await has_trace(phone, created_at) or await has_payment_trace_until_now(phone, created_at):
        return False

    variants = _phone_variants(phone)
    canonico = (variants[0] if variants else phone) + "@s.whatsapp.net"
    texto = await describe_image_bytes(img, phone=canonico)
    if not texto:
        return False

    meta = {"attachment_id": attachment_id, "conversation_id": conversation_id}
    await log_event(REPLAY_EVENT, canonico, meta)
    await log_event(RESCUED_EVENT, canonico, {**meta, "latency_s": round(time.monotonic() - started, 1)})
    await save_message(canonico, "user", texto)
    await process_message(canonico, texto)
    return True
