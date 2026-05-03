"""
WhatsApp Cloud API (Meta) client.

Environment variables required:
  WHATSAPP_TOKEN          — permanent access token
  WHATSAPP_PHONE_NUMBER_ID — phone number ID from Meta for Developers
"""
import os
import httpx

_GRAPH_URL = "https://graph.facebook.com/v19.0"


def _phone_number_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN', '')}",
        "Content-Type": "application/json",
    }


async def _send_via_meta(phone: str, text: str) -> None:
    number = phone.replace("@s.whatsapp.net", "")
    payload = {
        "messaging_product": "whatsapp",
        "to": number,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{_GRAPH_URL}/{_phone_number_id()}/messages",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()


async def send_text(phone: str, text: str) -> None:
    """Send a plain text message. Routes via Chatwoot if conversation is known, else Meta."""
    from app.chatwoot import get_conversation_id, send_message
    conversation_id = get_conversation_id(phone)
    if conversation_id is not None:
        await send_message(conversation_id, text)
    else:
        await _send_via_meta(phone, text)


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
