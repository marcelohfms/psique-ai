"""Tests for extract_message() and the /webhook endpoint."""
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import PHONE


def _msg(**kwargs) -> dict:
    """Build a minimal UAZAPI webhook payload."""
    defaults = {
        "fromMe": False,
        "wasSentByApi": False,
        "isGroup": False,
        "chatid": PHONE,
        "messageType": "Conversation",
        "text": "olá",
    }
    defaults.update(kwargs)
    return {"message": defaults}


# ── extract_message tests ─────────────────────────────────────────────────────

async def test_ignores_fromme():
    from app.main import extract_message
    result = await extract_message(_msg(fromMe=True))
    assert result is None


async def test_ignores_group():
    from app.main import extract_message
    result = await extract_message(_msg(isGroup=True))
    assert result is None


async def test_extracts_conversation_text():
    from app.main import extract_message
    result = await extract_message(_msg(text="Quero marcar consulta"))
    assert result == (PHONE, "Quero marcar consulta")


async def test_extended_text_includes_quoted_context():
    from app.main import extract_message
    payload = _msg(
        messageType="ExtendedTextMessage",
        text="Sim, isso mesmo",
        quoted="Confirma o horário das 9h?",
    )
    result = await extract_message(payload)
    assert result is not None
    phone, text = result
    assert phone == PHONE
    assert "Confirma o horário das 9h?" in text
    assert "Sim, isso mesmo" in text


async def test_ignores_missing_chatid():
    from app.main import extract_message
    result = await extract_message(_msg(chatid=""))
    assert result is None


async def test_ignores_empty_text():
    from app.main import extract_message
    result = await extract_message(_msg(text="   "))
    assert result is None


async def test_ignores_unknown_message_type():
    from app.main import extract_message
    result = await extract_message(_msg(messageType="StickerMessage"))
    assert result is None


# ── /webhook endpoint test ────────────────────────────────────────────────────

def test_webhook_returns_200_immediately(http_client):
    """The endpoint must respond 200 regardless of payload content."""
    response = http_client.post("/webhook", json=_msg())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
