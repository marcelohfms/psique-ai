"""Tests for extract_message() and the /webhook endpoint (Meta Cloud API format)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import PHONE


@pytest.fixture(autouse=True)
def clear_dedup_cache():
    """Reset the global deduplication cache between tests."""
    import app.main as _main
    _main._seen_msg_ids.clear()
    yield
    _main._seen_msg_ids.clear()

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


async def test_audio_message_sends_notice_and_returns_none():
    """Audio messages trigger a fixed reply and are not forwarded to Eva."""
    from app.main import extract_message
    with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
        result = await extract_message(_meta_payload(msg_type="audio"))
    assert result is None
    mock_send.assert_called_once()
    assert "áudio" in mock_send.call_args[0][1].lower()


async def test_extracts_image_payment_receipt():
    """Payment receipt images reach Eva (non-None text returned)."""
    from app.main import extract_message
    with patch("app.media.process_media", new_callable=AsyncMock, return_value="[imagem]: COMPROVANTE DE PAGAMENTO: R$100"):
        result = await extract_message(_meta_payload(msg_type="image"))
    assert result is not None
    _, text = result
    assert "COMPROVANTE" in text


async def test_medical_document_image_handled_directly():
    """Medical document images (exams, laudos) return None — Eva is skipped."""
    from app.main import extract_message
    # process_media returns None when document is already handled
    with patch("app.media.process_media", new_callable=AsyncMock, return_value=None):
        result = await extract_message(_meta_payload(msg_type="image"))
    assert result is None


async def test_medical_document_pdf_handled_directly():
    """Medical document PDFs return None — Eva is skipped."""
    from app.main import extract_message

    def _pdf_payload(from_number=_NUMBER):
        msg = {
            "from": from_number,
            "id": "wamid.pdf",
            "type": "document",
            "document": {"id": "pdf-789", "mime_type": "application/pdf"},
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
        }

    with patch("app.whatsapp.download_media", new_callable=AsyncMock, return_value=b"fake-pdf"):
        with patch("app.media.describe_pdf_bytes", new_callable=AsyncMock, return_value=None):
            result = await extract_message(_pdf_payload())
    assert result is None


async def test_payment_receipt_pdf_reaches_eva():
    """Payment receipt PDFs (comprovante) still reach Eva."""
    from app.main import extract_message

    def _pdf_payload(from_number=_NUMBER):
        msg = {
            "from": from_number,
            "id": "wamid.pdf2",
            "type": "document",
            "document": {"id": "pdf-abc", "mime_type": "application/pdf"},
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
        }

    comprovante_text = "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive.google.com/test]"
    with patch("app.whatsapp.download_media", new_callable=AsyncMock, return_value=b"fake-pdf"):
        with patch("app.media.describe_pdf_bytes", new_callable=AsyncMock, return_value=comprovante_text):
            result = await extract_message(_pdf_payload())
    assert result is not None
    _, text = result
    assert "COMPROVANTE" in text


async def test_pdf_processing_failure_notifies_clinic_only():
    """If PDF processing raises, the clinic must be notified (to follow up via a
    private Chatwoot note) and the patient must NOT be messaged directly by the bot —
    the message must never vanish without a trace."""
    from app.main import extract_message

    def _pdf_payload(from_number=_NUMBER):
        msg = {
            "from": from_number,
            "id": "wamid.pdf3",
            "type": "document",
            "document": {"id": "pdf-broken", "mime_type": "application/pdf"},
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
        }

    with patch("app.whatsapp.download_media", new_callable=AsyncMock, side_effect=Exception("boom")):
        with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
            with patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
                result = await extract_message(_pdf_payload())
    assert result is None
    mock_send.assert_not_called()
    mock_email.assert_called_once()


async def test_unrecognized_document_mime_notifies_clinic_only():
    """Documents with an unexpected mime_type (not 'pdf') must not be silently dropped,
    but also must not trigger a direct bot reply to the patient."""
    from app.main import extract_message

    msg = {
        "from": _NUMBER,
        "id": "wamid.doc1",
        "type": "document",
        "document": {"id": "doc-xyz", "mime_type": "application/octet-stream"},
    }
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
    }

    with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
        with patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
            result = await extract_message(payload)
    assert result is None
    mock_send.assert_not_called()
    mock_email.assert_called_once()


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
    message_type: int | str = "incoming",
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
    """Incoming patient messages via Chatwoot trigger Eva and register the conversation."""
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
        mock_push.assert_called_once()


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


async def test_chatwoot_webhook_processes_audio_attachment(async_client):
    payload = _chatwoot_payload(content="")
    payload["attachments"] = [
        {"file_type": "audio", "data_url": "https://storage.example.com/audio.ogg"}
    ]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock, return_value="[áudio transcrito]: consulta amanhã") as mock_att:
        mock_push.return_value = None
        mock_save.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_att.assert_called_once()
        mock_push.assert_called_once()


async def test_chatwoot_webhook_ignores_empty_attachment(async_client):
    payload = _chatwoot_payload(content="")
    payload["attachments"] = [{"file_type": "audio", "data_url": ""}]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock, return_value=None) as mock_att:
        mock_push.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


def _chatwoot_private_note_payload(
    content: str = "Marque a consulta com Dra. Bruna",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
    sender_type: str = "user",
    private: bool = True,
) -> dict:
    return {
        "id": 2,
        "content": content,
        "message_type": "outgoing",
        "private": private,
        "event": "message_created",
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": sender_type},
    }


async def test_chatwoot_private_note_from_agent_triggers_attendant_note(async_client):
    """A private note from a human agent (sender.type == 'user'/'agent') must be
    routed to Eva as an instruction and recorded in events for traceability."""
    with patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_private_note_payload(sender_type="user"),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_note.assert_called_once()
        mock_log.assert_not_called()  # only the ignored-sender path logs directly here


async def test_chatwoot_private_note_unexpected_sender_is_logged_not_dropped():
    """A private note whose sender.type isn't 'user'/'agent' (e.g. an unexpected
    Chatwoot sender shape) must not be silently dropped — it's persisted to the
    events table so the incident is diagnosable from the DB, without server logs."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_private_note_payload(sender_type="something_unexpected")
    with patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        await _handle_chatwoot_payload(payload)
    mock_note.assert_not_called()
    mock_log.assert_called_once()
    args = mock_log.call_args[0]
    assert args[0] == "attendant_note_ignored_unexpected_sender"
    assert args[1] == "5511999999999@s.whatsapp.net"


def _chatwoot_delivery_status_payload(
    status: str = "failed",
    content: str = "Olá! Esperamos que a consulta tenha sido boa!",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
    sender_type: str = "agent_bot",
    private: bool = False,
    external_error: str | None = "131049 - This message was not delivered to maintain healthy ecosystem engagement.",
) -> dict:
    return {
        "id": 99,
        "content": content,
        "message_type": "outgoing",
        "private": private,
        "status": status,
        "event": "message_updated",
        "content_attributes": {"external_error": external_error} if external_error else {},
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": sender_type},
    }


async def test_chatwoot_delivery_failure_logs_event_and_notifies_agent():
    """When Meta rejects a template send asynchronously (e.g. error 131049), Chatwoot
    reports it later via message_updated/status=failed. Since the original send call
    already got a 200 from Chatwoot and moved on (see app/chatwoot.py send_template_message),
    this is the only place such failures can be caught — it must log the event and alert
    the clinic via a private note instead of silently dropping it."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_delivery_status_payload()
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await _handle_chatwoot_payload(payload)

    mock_log.assert_called_once()
    args = mock_log.call_args[0]
    assert args[0] == "outbound_message_delivery_failed"
    assert args[1] == "5511999999999@s.whatsapp.net"
    assert args[2]["conversation_id"] == 42
    assert "131049" in args[2]["error"]

    mock_note.assert_called_once()
    note_args = mock_note.call_args[0]
    assert note_args[0] == 42
    assert "131049" in note_args[1]


async def test_chatwoot_delivery_status_sent_is_not_flagged():
    """A normal status update (e.g. status=sent/delivered) must not be treated as a
    failure — only status=failed triggers the alert."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_delivery_status_payload(status="delivered", external_error=None)
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await _handle_chatwoot_payload(payload)

    mock_log.assert_not_called()
    mock_note.assert_not_called()


async def test_chatwoot_delivery_failure_ignores_human_agent_messages():
    """A failed status on a message sent by a human agent (not our automation) should
    not trigger the alert — the agent already knows their own message failed."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_delivery_status_payload(sender_type="agent")
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await _handle_chatwoot_payload(payload)

    mock_log.assert_not_called()
    mock_note.assert_not_called()
