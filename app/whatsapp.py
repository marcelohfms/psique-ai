"""
Outbound WhatsApp messaging via Chatwoot.

Inbound media (audio/image) still arrives via the Meta Cloud API webhook,
so download_media() remains here using Meta's Graph API.

Environment variables required:
  WHATSAPP_TOKEN            — Meta permanent access token
  WHATSAPP_PHONE_NUMBER_ID  — Meta phone number ID (for template messages)
"""
import asyncio
import logging
import os
import httpx

_logger = logging.getLogger(__name__)

_GRAPH_URL = "https://graph.facebook.com/v22.0"

# Delay between bubbles when a reply is split into multiple messages, to
# mimic a person typing separate messages instead of dumping a wall of text.
_MESSAGE_SPLIT_DELAY_SECONDS = 1.2


def _phone_number_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


def _headers() -> dict:
    token = os.getenv("WHATSAPP_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _split_into_messages(text: str) -> list[str]:
    """Split a reply on blank lines into separate WhatsApp bubbles."""
    parts = [p.strip() for p in text.split("\n\n")]
    return [p for p in parts if p]


async def send_text(phone: str, text: str) -> None:
    """Send a plain text message via Chatwoot, creating a conversation if needed.

    Replies with blank-line-separated paragraphs are sent as separate WhatsApp
    bubbles, one per paragraph, instead of a single wall of text.

    Último filtro antes do paciente: nenhum endereço inventado passa daqui, venha
    de que nó ou script vier (os nós já sanitizam; isto cobre caminhos futuros).
    """
    from app.chatwoot import find_or_create_conversation, send_message, add_private_note
    from app.graph.prompts import sanitize_clinic_address

    conversation_id = await find_or_create_conversation(phone)

    safe_text, address_fixed = sanitize_clinic_address(text)
    if address_fixed:
        _logger.error(
            "GUARD_WRONG_ADDRESS: endereço inventado interceptado no send_text phone=%s original=%s",
            phone, text,
        )
        try:
            await add_private_note(
                conversation_id,
                "⚠️ A Eva tentou enviar um endereço que não é o da clínica. "
                "A mensagem foi corrigida automaticamente antes do envio.\n\n"
                f"Texto original: {text}",
            )
        except Exception:
            _logger.exception("Falha ao registrar nota privada do guard de endereço")
    text = safe_text

    parts = _split_into_messages(text) or [text]
    for i, part in enumerate(parts):
        if i > 0:
            await asyncio.sleep(_MESSAGE_SPLIT_DELAY_SECONDS)
        try:
            await send_message(conversation_id, part)
        except Exception as exc:
            # Chatwoot já esgotou os retries (rate limit, quota de token, 5xx).
            # Sem isto a falha some: o paciente simplesmente não recebe resposta
            # e ninguém fica sabendo. Registra em `events` e sobe o erro.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            _logger.error(
                "CHATWOOT_SEND_FAILED phone=%s conv=%s part=%s/%s status=%s: %s",
                phone, conversation_id, i + 1, len(parts), status, exc,
            )
            from app.database import log_event
            await log_event("chatwoot_send_failed", phone, {
                "conversation_id": conversation_id,
                "status_code": status,
                "part": i + 1,
                "total_parts": len(parts),
                "error": str(exc)[:500],
            })
            raise


async def send_template(phone: str, template_name: str, language: str, components: list) -> None:
    """Send a WhatsApp template message via Meta Cloud API."""
    number = phone.replace("@s.whatsapp.net", "")
    payload = {
        "messaging_product": "whatsapp",
        "to": number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": components,
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{_GRAPH_URL}/{_phone_number_id()}/messages",
            json=payload,
            headers=_headers(),
        )
        if not response.is_success:
            raise httpx.HTTPStatusError(
                f"{response.status_code} — {response.text}",
                request=response.request,
                response=response,
            )


async def download_media(media_id: str) -> bytes:
    """Download media bytes given a Meta media_id."""
    token = os.getenv("WHATSAPP_TOKEN", "")
    auth = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{_GRAPH_URL}/{media_id}", headers=auth)
        resp.raise_for_status()
        url = resp.json().get("url")
        if not url:
            raise ValueError(f"No URL returned for media_id={media_id}")
        media_resp = await client.get(url, headers=auth, follow_redirects=True)
        media_resp.raise_for_status()
        return media_resp.content
