"""Rede de segurança contra comprovante mudo.

Um comprovante enviado como imagem sem legenda pode sumir por completo quando o webhook
da Meta não chega até nós: sem linha em `messages`, sem evento, sem e-mail, sem resposta.
O PR #295 só cobre a falha que acontece DENTRO do nosso código; quando a requisição nem
bate no servidor, nenhum conserto do webhook ajuda. Casos Maria Luiza, Virgínia, Lara e
Luciana (ver memory comprovante-imagem-dropado-na-ingestao-meta).

Este módulo é a rede: o cron `scripts/reconcile_dropped_receipts` roda `reconcile` a cada
15 min, lista os anexos recebidos no Chatwoot (que recebe a mídia por um canal confiável),
cruza com o nosso banco e, para todo comprovante ou documento sem rastro, manda e-mail à
clínica tratar pelo painel. Nunca registra pagamento sozinho — o risco de valor errado ou
comprovante reusado é alto demais (ver register_payment e os casos de reuso/parcial).
"""
import logging
from datetime import datetime, timezone

import httpx

from app.chatwoot import list_recent_incoming_attachments
from app.database import get_supabase, log_event
from app.email_sender import send_clinic_notification_email
from app.media import classify_image_raw
from app.phone import _phone_variants

logger = logging.getLogger(__name__)

RECEIPT_DROP_EVENT = "receipt_drop_alerted"
# Tolerância em torno da chegada do anexo: um registro (pela Eva ou pelo painel) que
# aconteça até 15 min ANTES já conta como rastro, cobrindo folga de relógio.
TRACE_TOLERANCE_S = 15 * 60
_MESSAGE_MARKERS = ("[imagem]", "Recebemos seu documento")
_RECEIPT_EVENTS = ("payment_receipt_registered", "payment_receipt_unreadable")


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat()


async def has_trace(phone: str, created_at: int) -> bool:
    """True se o anexo já deixou rastro no nosso banco: uma linha-espelho em `messages`
    (marcador `[imagem]:` ou "Recebemos seu documento") ou um evento de pagamento, para
    qualquer variante do telefone, a partir de ~15 min antes da chegada do anexo.

    O painel grava os dois (evento `payment_receipt_registered` + linha em `messages`),
    então comprovante já resolvido pela clínica cai aqui e não vira alerta.
    """
    variants = _phone_variants(phone)
    lower = _iso(created_at - TRACE_TOLERANCE_S)
    client = await get_supabase()

    msgs = (
        await client.from_("messages").select("id")
        .in_("phone", variants).gte("created_at", lower)
        .or_("content.ilike.%[imagem]%,content.ilike.%Recebemos seu documento%")
        .limit(1).execute()
    )
    if msgs.data:
        return True

    evs = (
        await client.from_("events").select("id")
        .in_("phone", variants).gte("created_at", lower)
        .in_("event_type", list(_RECEIPT_EVENTS))
        .limit(1).execute()
    )
    return bool(evs.data)


async def already_alerted(attachment_id: int) -> bool:
    """True se este anexo já gerou um alerta numa rodada anterior — evita cutucar a
    clínica a cada 15 min sobre o mesmo comprovante."""
    client = await get_supabase()
    r = (
        await client.from_("events").select("id")
        .eq("event_type", RECEIPT_DROP_EVENT)
        .contains("metadata", {"attachment_id": attachment_id})
        .limit(1).execute()
    )
    return bool(r.data)


async def _download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as hc:
        resp = await hc.get(url)
        resp.raise_for_status()
        return resp.content


async def _alert_clinic(cand: dict, kind: str) -> None:
    phone = cand.get("phone", "")
    cid = cand.get("conversation_id")
    tipo = "comprovante de pagamento" if kind == "payment" else "documento"
    await send_clinic_notification_email(
        f"Possível {tipo} não processado — {phone}",
        f"Número: {phone}\nConversa Chatwoot: #{cid}\n\n"
        f"O paciente enviou uma IMAGEM que parece {tipo}, mas ela não chegou a ser "
        "processada pela Eva (sem rastro no sistema). Provável falha de entrega do "
        "webhook da Meta.\n\n"
        "Por favor, abra a conversa no Chatwoot e trate manualmente (ex.: registre o "
        "comprovante pelo painel), em vez de pedir para o paciente reenviar.",
    )


async def reconcile(now: datetime, *, floor_min: int = 10, window_min: int = 60) -> int:
    """Cruza o Chatwoot com o banco e alerta a clínica sobre comprovante/documento sem
    rastro. Olha anexos entre `floor_min` e `window_min` minutos de idade — o piso evita
    alarmar algo que acabou de chegar e ainda pode estar em processamento. Retorna quantos
    alertas disparou."""
    now_ts = int(now.timestamp())
    until_ts = now_ts - floor_min * 60
    since_ts = now_ts - window_min * 60

    candidates = await list_recent_incoming_attachments(since_ts, until_ts)
    alerts = 0
    for cand in candidates:
        aid = cand.get("attachment_id")
        if await already_alerted(aid):
            continue
        if await has_trace(cand.get("phone", ""), cand.get("created_at", 0)):
            continue
        try:
            img = await _download_bytes(cand.get("data_url", ""))
        except Exception:
            logger.exception("RECONCILE_DOWNLOAD_FAILED attachment_id=%s", aid)
            continue
        kind, _canonical = await classify_image_raw(img, cand.get("phone", ""))
        if kind not in ("payment", "document"):
            continue
        await _alert_clinic(cand, kind)
        await log_event(RECEIPT_DROP_EVENT, cand.get("phone", ""), {
            "attachment_id": aid,
            "conversation_id": cand.get("conversation_id"),
            "kind": kind,
        })
        alerts += 1

    logger.info("RECONCILE done candidates=%d alerts=%d", len(candidates), alerts)
    return alerts
