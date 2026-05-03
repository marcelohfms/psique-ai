"""Unit tests for app/chatwoot.py."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx


def test_register_and_get_conversation():
    from app.chatwoot import register_conversation, get_conversation_id, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 42)
    assert get_conversation_id("5511999999999@s.whatsapp.net") == 42


def test_get_conversation_unknown_phone():
    from app.chatwoot import get_conversation_id, _store
    _store.clear()
    assert get_conversation_id("5500000000000@s.whatsapp.net") is None


async def test_send_message_calls_chatwoot_api():
    from app.chatwoot import send_message
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {
            "CHATWOOT_BASE_URL": "https://chat.example.com",
            "CHATWOOT_ACCOUNT_ID": "1",
            "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
        }):
            await send_message(conversation_id=42, text="Olá!")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "/conversations/42/messages" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["content"] == "Olá!"


async def test_unassign_agent_bot_calls_api():
    from app.chatwoot import unassign_agent_bot
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {
            "CHATWOOT_BASE_URL": "https://chat.example.com",
            "CHATWOOT_ACCOUNT_ID": "1",
            "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
        }):
            await unassign_agent_bot(conversation_id=42)

        mock_client.delete.assert_called_once()
        call_url = mock_client.delete.call_args[0][0]
        assert "/conversations/42/assignments" in call_url
