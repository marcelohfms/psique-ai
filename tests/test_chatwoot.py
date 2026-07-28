"""Unit tests for app/chatwoot.py."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, call
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


def test_split_into_messages_splits_on_blank_lines():
    from app.whatsapp import _split_into_messages
    text = "Oi! Tudo bem?\n\nVou te ajudar com o agendamento.\n\nQual dia prefere?"
    assert _split_into_messages(text) == [
        "Oi! Tudo bem?",
        "Vou te ajudar com o agendamento.",
        "Qual dia prefere?",
    ]


def test_split_into_messages_drops_empty_segments():
    from app.whatsapp import _split_into_messages
    text = "Primeira parte\n\n\n\nSegunda parte\n\n   \n\nTerceira parte"
    assert _split_into_messages(text) == ["Primeira parte", "Segunda parte", "Terceira parte"]


def test_split_into_messages_single_paragraph_unchanged():
    from app.whatsapp import _split_into_messages
    assert _split_into_messages("Só uma linha, sem quebra.") == ["Só uma linha, sem quebra."]


async def test_send_text_splits_multi_paragraph_reply_into_separate_messages():
    """A reply with blank-line-separated paragraphs is sent as multiple bubbles."""
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 99)

    with patch("app.chatwoot.send_message", new_callable=AsyncMock) as mock_send, \
         patch("app.whatsapp.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        from app.whatsapp import send_text
        await send_text(
            "5511999999999@s.whatsapp.net",
            "Oi! Tudo bem?\n\nVou verificar os horários disponíveis para você.",
        )

        assert mock_send.await_args_list == [
            call(99, "Oi! Tudo bem?"),
            call(99, "Vou verificar os horários disponíveis para você."),
        ]
        mock_sleep.assert_awaited_once()


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


async def test_send_text_sanitizes_invented_clinic_address():
    """Rede final: qualquer texto que chegue ao paciente passa pelo sanitizador de
    endereço, mesmo vindo de um caminho novo (cron, script, nó futuro)."""
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 99)

    with patch("app.chatwoot.send_message", new_callable=AsyncMock) as mock_send, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note, \
         patch("app.whatsapp.asyncio.sleep", new_callable=AsyncMock):
        from app.whatsapp import send_text
        await send_text(
            "5511999999999@s.whatsapp.net",
            "A clínica fica na Rua dos Jacarandás, 100, no bairro Jardim das Flores.",
        )

    sent = " ".join(c.args[1] for c in mock_send.await_args_list)
    assert "Jacarandás" not in sent
    assert "República do Líbano, 251" in sent
    # a clínica precisa saber que a Eva alucinou — nota privada, não mensagem ao paciente
    mock_note.assert_awaited_once()
    assert "Jacarandás" in mock_note.await_args.args[1]


# ── Rate limiting / retry (Chatwoot 4.16+) ────────────────────────────────────


def _http_resp(status_code: int = 200, json_body: dict | None = None, headers: dict | None = None):
    """A response mock with a real status_code, so the retry logic actually branches."""
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    r.json = MagicMock(return_value=json_body or {})
    if status_code >= 400:
        r.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=r,
        ))
    else:
        r.raise_for_status = MagicMock()
    return r


async def test_request_retries_on_429_then_succeeds():
    """A throttled call is retried instead of blowing up the whole reply."""
    from app.chatwoot import _request
    client = AsyncMock()
    client.post = AsyncMock(side_effect=[
        _http_resp(429, headers={"Retry-After": "0"}),
        _http_resp(200, {"ok": True}),
    ])

    with patch("app.chatwoot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await _request(client, "POST", "https://chat.example.com/x", json={})

    assert resp.status_code == 200
    assert client.post.await_count == 2
    mock_sleep.assert_awaited_once_with(0.0)  # honours Retry-After, not the backoff


async def test_request_honours_backoff_when_no_retry_after_header():
    from app.chatwoot import _request, _BACKOFF_BASE_SECONDS
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[_http_resp(503), _http_resp(200)])

    with patch("app.chatwoot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await _request(client, "GET", "https://chat.example.com/x")

    mock_sleep.assert_awaited_once_with(_BACKOFF_BASE_SECONDS)


async def test_request_gives_up_after_max_attempts():
    from app.chatwoot import _request, _MAX_ATTEMPTS
    client = AsyncMock()
    client.post = AsyncMock(return_value=_http_resp(429, headers={"Retry-After": "0"}))

    with patch("app.chatwoot.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError):
            await _request(client, "POST", "https://chat.example.com/x", json={})

    assert client.post.await_count == _MAX_ATTEMPTS


async def test_request_does_not_retry_client_errors():
    """A 403 (bad token / quota) is a hard failure — retrying just burns quota."""
    from app.chatwoot import _request
    client = AsyncMock()
    client.get = AsyncMock(return_value=_http_resp(403))

    with pytest.raises(httpx.HTTPStatusError):
        await _request(client, "GET", "https://chat.example.com/x")

    assert client.get.await_count == 1


async def test_request_tolerates_expected_status():
    """unassign_agent_bot's 404 must not raise."""
    from app.chatwoot import _request
    client = AsyncMock()
    client.delete = AsyncMock(return_value=_http_resp(404))

    resp = await _request(client, "DELETE", "https://chat.example.com/x", tolerate=(404,))

    assert resp.status_code == 404
    resp.raise_for_status.assert_not_called()


async def test_find_or_create_fetches_contact_conversations_once():
    """The contact-conversations endpoint used to be hit twice per lookup — once for
    the diagnostic log, once to pick the conversation. One fetch must serve both."""
    from app.chatwoot import find_or_create_conversation, _store
    _store.clear()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    conv_calls = []

    async def fake_get(url: str, **_kw):
        if "/contacts/search" in url:
            return _http_resp(200, {"payload": [{"id": 222, "contact_inboxes": [{"source_id": "x"}]}]})
        if "/conversations" in url:
            conv_calls.append(url)
            return _http_resp(200, {"payload": [{"id": 333, "inbox_id": 1, "status": "open"}]})
        raise AssertionError(f"unexpected GET {url}")

    mock_client.get = AsyncMock(side_effect=fake_get)

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict("os.environ", {
             "CHATWOOT_BASE_URL": "https://chat.example.com",
             "CHATWOOT_ACCOUNT_ID": "1",
             "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
             "CHATWOOT_INBOX_ID": "1",
         }):
        result = await find_or_create_conversation("5511777777777@s.whatsapp.net")

    assert result == 333
    assert len(conv_calls) == 1


async def test_send_text_logs_event_when_chatwoot_send_fails():
    """A send that Chatwoot rejects after retries must not fail silently."""
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 99)

    failure = httpx.HTTPStatusError("429", request=MagicMock(), response=_http_resp(429))

    with patch("app.chatwoot.send_message", new_callable=AsyncMock, side_effect=failure), \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event:
        from app.whatsapp import send_text
        with pytest.raises(httpx.HTTPStatusError):
            await send_text("5511999999999@s.whatsapp.net", "Oi!")

    mock_log_event.assert_awaited_once()
    event_type, phone, metadata = mock_log_event.await_args.args
    assert event_type == "chatwoot_send_failed"
    assert phone == "5511999999999@s.whatsapp.net"
    assert metadata["status_code"] == 429
    assert metadata["conversation_id"] == 99


async def test_send_text_does_not_alert_on_normal_message():
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 99)

    with patch("app.chatwoot.send_message", new_callable=AsyncMock) as mock_send, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        from app.whatsapp import send_text
        await send_text("5511999999999@s.whatsapp.net", "Oi! Tudo bem?")

    mock_note.assert_not_awaited()
    assert mock_send.await_args.args[1] == "Oi! Tudo bem?"
