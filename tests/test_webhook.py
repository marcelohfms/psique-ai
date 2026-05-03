"""Tests for extract_message() and the /webhook endpoint (Meta Cloud API format)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import PHONE

# Strip @s.whatsapp.net to get the raw number Meta sends
_NUMBER = PHONE.replace("@s.whatsapp.net", "")


def _meta_payload(
    msg_type: str = "text",
    body: str = "olá",
    from_number: str = _NUMBER,
    include_messages: bool = True,
) -> dict:
    """Build a minimal Meta Cloud API webhook payload."""
    value: dict = {}
    if include_messages:
        msg: dict = {"from": from_number, "id": "wamid.test", "type": msg_type}
        if msg_type == "text":
            msg["text"] = {"body": body}
        elif msg_type == "audio":
            msg["audio"] = {"id": "media-123", "mime_type": "audio/ogg; codecs=opus"}
        elif msg_type == "image":
            msg["image"] = {"id": "media-456", "mime_type": "image/jpeg"}
        value["messages"] = [msg]

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-id", "changes": [{"value": value, "field": "messages"}]}],
    }


def _status_payload() -> dict:
    """Build a Meta delivery status payload (no messages key)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-id", "changes": [{"value": {"statuses": [{"id": "wamid.test", "status": "delivered"}]}, "field": "messages"}]}],
    }


# ── extract_message tests ─────────────────────────────────────────────────────

async def test_extracts_text_message():
    from app.main import extract_message
    result = await extract_message(_meta_payload(body="Quero marcar consulta"))
    assert result is not None
    phone, text = result
    assert phone == PHONE
    assert text == "Quero marcar consulta"


async def test_ignores_status_payload():
    from app.main import extract_message
    result = await extract_message(_status_payload())
    assert result is None


async def test_ignores_empty_messages():
    from app.main import extract_message
    result = await extract_message(_meta_payload(include_messages=False))
    assert result is None


async def test_ignores_empty_text():
    from app.main import extract_message
    result = await extract_message(_meta_payload(body="   "))
    assert result is None


async def test_ignores_unknown_message_type():
    from app.main import extract_message
    result = await extract_message(_meta_payload(msg_type="sticker"))
    assert result is None


async def test_ignores_missing_from():
    from app.main import extract_message
    result = await extract_message(_meta_payload(from_number=""))
    assert result is None


async def test_extracts_audio_message():
    from app.main import extract_message
    with patch("app.media.process_media", new_callable=AsyncMock, return_value="[áudio transcrito]: consulta amanhã"):
        result = await extract_message(_meta_payload(msg_type="audio"))
    assert result is not None
    phone, text = result
    assert phone == PHONE
    assert "áudio transcrito" in text


async def test_extracts_image_message():
    from app.main import extract_message
    with patch("app.media.process_media", new_callable=AsyncMock, return_value="[imagem]: COMPROVANTE DE PAGAMENTO: R$100"):
        result = await extract_message(_meta_payload(msg_type="image"))
    assert result is not None
    _, text = result
    assert "COMPROVANTE" in text


# ── /webhook endpoint tests ───────────────────────────────────────────────────

def test_webhook_post_returns_200(http_client):
    """POST /webhook must respond 200 immediately."""
    response = http_client.post("/webhook", json=_meta_payload())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_verify_get_returns_challenge(http_client):
    """GET /webhook must respond with the hub.challenge when token matches."""
    import os
    token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    response = http_client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "abc123"},
    )
    assert response.status_code == 200
    assert response.text == "abc123"


def test_webhook_verify_get_rejects_wrong_token(http_client):
    """GET /webhook must respond 403 when token doesn't match."""
    response = http_client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "abc123"},
    )
    assert response.status_code == 403


# ── Chatwoot webhook tests ────────────────────────────────────────────────────

def _chatwoot_payload(
    content: str = "olá",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
    message_type: int = 0,
) -> dict:
    return {
        "id": 1,
        "content": content,
        "message_type": message_type,
        "event": "message_created",
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": "contact"},
    }


async def test_chatwoot_webhook_processes_incoming_message(async_client):
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.chatwoot.register_conversation") as mock_register:
        mock_push.return_value = None
        mock_save.return_value = None
        mock_register.return_value = None

        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_payload(content="Quero marcar consulta"),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_register.assert_called_once_with("5511999999999@s.whatsapp.net", 42)


async def test_chatwoot_webhook_ignores_outgoing_messages(async_client):
    with patch("app.main.buffer_push") as mock_push:
        mock_push.return_value = None
        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_payload(message_type=1),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


async def test_chatwoot_webhook_ignores_activity_messages(async_client):
    with patch("app.main.buffer_push") as mock_push:
        mock_push.return_value = None
        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_payload(message_type=2),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


async def test_chatwoot_webhook_ignores_missing_content(async_client):
    payload = _chatwoot_payload()
    payload["content"] = ""
    with patch("app.main.buffer_push") as mock_push:
        mock_push.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()
