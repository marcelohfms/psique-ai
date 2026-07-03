from unittest.mock import AsyncMock, MagicMock, patch

import chatwoot_client


async def test_send_confirmation_message_posts_to_chatwoot(monkeypatch):
    monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chatwoot.example.com")
    monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.setenv("CHATWOOT_AGENT_BOT_TOKEN", "bot-token-123")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = mock_post

    with patch("chatwoot_client.httpx.AsyncClient", return_value=mock_client):
        await chatwoot_client.send_confirmation_message(42, "Recebemos seu pagamento!")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://chatwoot.example.com/api/v1/accounts/1/conversations/42/messages"
    assert kwargs["json"] == {"content": "Recebemos seu pagamento!", "message_type": "outgoing"}
    assert kwargs["headers"]["api_access_token"] == "bot-token-123"
