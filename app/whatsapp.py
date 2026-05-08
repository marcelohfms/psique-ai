"""
Outbound WhatsApp messaging via Chatwoot.

Inbound media (audio/image) still arrives via the Meta Cloud API webhook,
so download_media() remains here using Meta's Graph API.

Environment variables required:
  WHATSAPP_TOKEN            — Meta permanent access token
  WHATSAPP_PHONE_NUMBER_ID  — Meta phone number ID (for template messages)
"""
import os
import httpx

_GRAPH_URL = "https://graph.facebook.com/v19.0"


def _phone_number_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


def _headers() -> dict:
    token = os.getenv("WHATSAPP_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def send_text(phone: str, text: str) -> None:
    """Send a plain text message via Chatwoot, creating a conversation if needed."""
    from app.chatwoot import find_or_create_conversation, send_message
    conversation_id = await find_or_create_conversation(phone)
    await send_message(conversation_id, text)


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
        response.raise_for_status()


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
