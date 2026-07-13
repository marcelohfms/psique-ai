import os

# Override any .env values with test stubs BEFORE any app module is imported.
# This prevents local .env (with real credentials) from leaking into tests
# and causing real API calls / emails / DB writes during test runs.
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_KEY"] = "test-key"
os.environ["SUPABASE_CONNECTION_STRING"] = ""
os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["GOOGLE_REFRESH_TOKEN"] = "test-refresh-token"
os.environ["WHATSAPP_TOKEN"] = "test-token"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "123456789"
os.environ["WHATSAPP_VERIFY_TOKEN"] = "test-verify-token"
os.environ["META_APP_SECRET"] = "test-app-secret"
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["CLINIC_NOTIFY_EMAIL"] = ""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

PHONE = "5583999999999@s.whatsapp.net"
CONFIG = {"configurable": {"phone": PHONE, "thread_id": PHONE}}


def make_supabase_client():
    """Return a MagicMock that behaves like a chainable Supabase AsyncClient."""
    execute = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for method in ("select", "eq", "limit", "single", "maybe_single",
                   "gte", "order", "insert", "update", "upsert"):
        getattr(table, method).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


@pytest.fixture
def mock_supabase():
    """Patch app.database.get_supabase; yields (client, table, execute)."""
    client, table, execute = make_supabase_client()
    with patch("app.database.get_supabase", new_callable=AsyncMock) as mock:
        mock.return_value = client
        yield client, table, execute


@pytest.fixture
def mock_send_text():
    with patch("app.whatsapp.send_text", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_chatbot():
    """Patch app.graph.graph.chatbot with empty state + no-op ainvoke."""
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={}))
    chatbot.ainvoke = AsyncMock(return_value={})
    with patch("app.graph.graph.chatbot", chatbot):
        yield chatbot


@pytest.fixture
def http_client(mock_chatbot):
    """Synchronous TestClient for the FastAPI app (no Supabase checkpointer)."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
async def async_client(mock_chatbot):
    """Async HTTPX client for tests that use asyncio.create_task."""
    import httpx
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
