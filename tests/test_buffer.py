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
    buf._phone_locks.clear()
    yield
    buf._pending.clear()
    buf._phone_locks.clear()


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


async def test_messages_arriving_during_invoke_join_the_same_turn():
    """Caso Dr. Paulo Diniz (28/07): comprovante + 'Pix enviado' + 'Saldo restante
    pago'. As duas últimas chegaram enquanto o invoke do comprovante ainda rodava e
    cada uma virou um turno separado (3 respostas da Eva). Devem virar UM turno."""
    from app.buffer import push, get_phone_lock

    handler = AsyncMock()
    lock = get_phone_lock(PHONE)

    with patch("app.buffer.DEBOUNCE_SECONDS", 0), \
         patch("app.buffer._DEFER_RETRY_SECONDS", 0.01):
        # Simula o invoke do comprovante em andamento
        await lock.acquire()
        try:
            await push(PHONE, "Pix enviado", handler)
            await push(PHONE, "Saldo restante pago", handler)
            await asyncio.sleep(0.1)
            # Nada pode ser despachado enquanto o invoke está em voo
            handler.assert_not_awaited()
        finally:
            lock.release()
        await asyncio.sleep(0.1)

    handler.assert_awaited_once()
    combined_text = handler.call_args[0][1]
    assert "Pix enviado" in combined_text
    assert "Saldo restante pago" in combined_text


async def test_deferral_cap_dispatches_even_if_lock_never_released():
    """Um invoke travado não pode engolir a mensagem em silêncio: passado o teto,
    o buffer despacha mesmo com o lock preso."""
    from app.buffer import push, get_phone_lock

    handler = AsyncMock()
    lock = get_phone_lock(PHONE)

    with patch("app.buffer.DEBOUNCE_SECONDS", 0), \
         patch("app.buffer._DEFER_RETRY_SECONDS", 0.01), \
         patch("app.buffer._MAX_DEFER_SECONDS", 0.05):
        await lock.acquire()
        try:
            await push(PHONE, "alguém aí?", handler)
            await asyncio.sleep(0.3)
        finally:
            lock.release()

    handler.assert_awaited_once_with(PHONE, "alguém aí?")


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
