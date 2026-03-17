"""Tests for the debounce buffer in app/buffer.py."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

PHONE = "5583999999999@s.whatsapp.net"


@pytest.fixture(autouse=True)
def clear_buffer():
    """Reset module-level _pending dict between tests."""
    import app.buffer as buf
    buf._pending.clear()
    yield
    buf._pending.clear()


async def test_single_message_delivered_after_debounce():
    handler = AsyncMock()
    with patch("app.buffer.DEBOUNCE_SECONDS", 0):
        from app.buffer import push
        await push(PHONE, "olá", handler)
        # Let the scheduled task run
        await asyncio.sleep(0.05)
    handler.assert_awaited_once_with(PHONE, "olá")


async def test_second_message_cancels_first_timer():
    """Sending two messages quickly should result in only one handler call."""
    handler = AsyncMock()
    with patch("app.buffer.DEBOUNCE_SECONDS", 0):
        from app.buffer import push
        await push(PHONE, "primeira", handler)
        await push(PHONE, "segunda", handler)
        await asyncio.sleep(0.05)
    handler.assert_awaited_once()


async def test_rapid_messages_coalesced_into_single_call():
    """Multiple rapid messages must be combined in a single handler invocation."""
    handler = AsyncMock()
    with patch("app.buffer.DEBOUNCE_SECONDS", 0):
        from app.buffer import push
        await push(PHONE, "oi", handler)
        await push(PHONE, "quero", handler)
        await push(PHONE, "marcar", handler)
        await asyncio.sleep(0.05)
    handler.assert_awaited_once()
    combined_text = handler.call_args[0][1]
    assert "oi" in combined_text
    assert "quero" in combined_text
    assert "marcar" in combined_text


async def test_handler_error_does_not_propagate():
    """An exception raised by the handler must not crash the buffer flush."""
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.buffer.DEBOUNCE_SECONDS", 0):
        from app.buffer import push
        # If the exception propagates, this test will fail/error
        await push(PHONE, "mensagem", handler)
        # The flush runs in a background task; give it time to complete
        await asyncio.sleep(0.05)
    # We just verify the test reaches here without raising
    handler.assert_awaited_once()
