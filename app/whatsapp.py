"""
Outbound WhatsApp messaging via Chatwoot.

Inbound media (audio/image) still arrives via the Meta Cloud API webhook,
so download_media() remains here using Meta's Graph API.

Environment variables required:
  WHATSAPP_TOKEN            — Meta permanent access token
  WHATSAPP_PHONE_NUMBER_ID  — Meta phone number ID (for template messages)
"""
import asyncio
import os
import httpx

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
    """
    from app.chatwoot import find_or_create_conversation, send_message
    conversation_id = await find_or_create_conversation(phone)
    parts = _split_into_messages(text) or [text]
    for i, part in enumerate(parts):
        if i > 0:
            await asyncio.sleep(_MESSAGE_SPLIT_DELAY_SECONDS)
        await send_message(conversation_id, part)


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
