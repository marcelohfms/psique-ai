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


async def test_send_text_uses_cached_conversation():
    """send_text uses the in-memory store when the conversation is already known."""
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 99)

    with patch("app.chatwoot.send_message", new_callable=AsyncMock) as mock_send:
        from app.whatsapp import send_text
        await send_text("5511999999999@s.whatsapp.net", "Testando")
        mock_send.assert_called_once_with(99, "Testando")


async def test_send_text_resolves_via_chatwoot_when_unknown():
    """send_text falls through to find_or_create_conversation for unknown phones."""
    from app.chatwoot import _store
    _store.clear()

    with patch("app.chatwoot.find_or_create_conversation", new_callable=AsyncMock, return_value=123) as mock_resolve, \
         patch("app.chatwoot.send_message", new_callable=AsyncMock) as mock_send:
        from app.whatsapp import send_text
        await send_text("5583998566516@s.whatsapp.net", "Notificação interna")

        mock_resolve.assert_awaited_once_with("5583998566516@s.whatsapp.net")
        mock_send.assert_awaited_once_with(123, "Notificação interna")


async def test_find_or_create_returns_cached_conversation():
    """find_or_create_conversation short-circuits to the cached id without calling Chatwoot."""
    from app.chatwoot import find_or_create_conversation, register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 77)

    with patch("httpx.AsyncClient") as mock_cls:
        result = await find_or_create_conversation("5511999999999@s.whatsapp.net")

    assert result == 77
    mock_cls.assert_not_called()


async def test_find_or_create_raises_when_no_conversation_exists():
    """When a contact exists but has no conversations, raise RuntimeError (can't create for WhatsApp inboxes)."""
    from app.chatwoot import find_or_create_conversation, _store
    import pytest
    _store.clear()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def _resp(json_body: dict):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=json_body)
        return r

    async def fake_get(url: str, **_kw):
        if "/contacts/search" in url:
            return _resp({"payload": [{"id": 555}]})  # contact found
        if "/conversations" in url:
            return _resp({"payload": []})  # no conversations
        raise AssertionError(f"unexpected GET {url}")

    mock_client.get = AsyncMock(side_effect=fake_get)

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict("os.environ", {
             "CHATWOOT_BASE_URL": "https://chat.example.com",
             "CHATWOOT_ACCOUNT_ID": "1",
             "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
             "CHATWOOT_INBOX_ID": "1",
         }):
        with pytest.raises(RuntimeError, match="No Chatwoot conversation found"):
            await find_or_create_conversation("5583998566516@s.whatsapp.net")


async def test_find_or_create_reuses_existing_open_conversation():
    """When the contact already has an open conversation in the inbox, reuse it."""
    from app.chatwoot import find_or_create_conversation, _store
    _store.clear()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def _resp(json_body: dict):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=json_body)
        return r

    async def fake_get(url: str, **_kw):
        if "/contacts/search" in url:
            return _resp({"payload": [{"id": 222}]})
        if "/conversations" in url:
            return _resp({"payload": [{"id": 333, "inbox_id": 1, "status": "open"}]})
        raise AssertionError(f"unexpected GET {url}")

    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.post = AsyncMock()

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict("os.environ", {
             "CHATWOOT_BASE_URL": "https://chat.example.com",
             "CHATWOOT_ACCOUNT_ID": "1",
             "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
             "CHATWOOT_INBOX_ID": "1",
         }):
        result = await find_or_create_conversation("5511777777777@s.whatsapp.net")

    assert result == 333
    mock_client.post.assert_not_called()


async def test_find_or_create_skips_duplicate_contact_without_conversation():
    """A stray duplicate Chatwoot contact (e.g. registered with the extra 9 in the
    phone number, no linked conversation) must not shadow the real contact that
    matches the other phone-digit variant and has the actual conversation."""
    from app.chatwoot import find_or_create_conversation, _store
    _store.clear()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def _resp(json_body: dict):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=json_body)
        return r

    async def fake_get(url: str, **kwargs):
        if "/contacts/search" in url:
            q = kwargs.get("params", {}).get("q")
            if q == "5581999735649":
                return _resp({"payload": [{"id": 89, "contact_inboxes": []}]})
            if q == "558199735649":
                return _resp({"payload": [{"id": 90, "contact_inboxes": [{"source_id": "558199735649"}]}]})
            return _resp({"payload": []})
        if "/contacts/90/conversations" in url:
            return _resp({"payload": [{"id": 333, "inbox_id": 1, "status": "open"}]})
        if "/contacts/89/conversations" in url:
            return _resp({"payload": []})
        raise AssertionError(f"unexpected GET {url}")

    mock_client.get = AsyncMock(side_effect=fake_get)

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict("os.environ", {
             "CHATWOOT_BASE_URL": "https://chat.example.com",
             "CHATWOOT_ACCOUNT_ID": "1",
             "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
             "CHATWOOT_INBOX_ID": "1",
         }):
        result = await find_or_create_conversation("5581999735649@s.whatsapp.net")

    assert result == 333
