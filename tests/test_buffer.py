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


async def test_replays_paulo_diniz_incident_end_to_end():
    """Replay da sequência real de 28/07/2026 (5581988521442), com o handler
    tomando o lock como o process_message faz, e os tempos reais divididos por 40:

        13:05:42.3  comprovante entra no buffer
        13:05:45    invoke do comprovante começa (debounce de 3s)
        13:06:02.9  "Pix enviado"          — invoke ainda rodando
        13:06:08.5  "Saldo restante pago"  — invoke ainda rodando
        13:06:14.1  invoke do comprovante termina (29s: Drive + Sheets)

    Antes: 3 turnos → 3 respostas da Eva. Depois: 2 turnos — o comprovante e,
    num único turno, as duas linhas que chegaram durante o invoke."""
    from app.buffer import push, get_phone_lock

    SCALE = 1 / 40
    turns: list[str] = []

    async def fake_process_message(_phone: str, text: str) -> None:
        turns.append(text)
        # process_message segura o lock durante todo o ainvoke (app/main.py:486)
        async with get_phone_lock(_phone):
            await asyncio.sleep(29 * SCALE if "COMPROVANTE" in text else 5 * SCALE)

    with patch("app.buffer.DEBOUNCE_SECONDS", 3 * SCALE), \
         patch("app.buffer._DEFER_RETRY_SECONDS", 1 * SCALE):
        await push(PHONE, "[imagem]: COMPROVANTE DE PAGAMENTO R$ 600,00", fake_process_message)
        await asyncio.sleep(20.6 * SCALE)
        await push(PHONE, "Pix enviado", fake_process_message)
        await asyncio.sleep(5.6 * SCALE)
        await push(PHONE, "Saldo restante pago", fake_process_message)
        await asyncio.sleep(40 * SCALE)

    assert len(turns) == 2, f"esperava 2 turnos, veio {len(turns)}: {turns}"
    assert "COMPROVANTE" in turns[0]
    assert "Pix enviado" in turns[1] and "Saldo restante pago" in turns[1]


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


async def test_drain_flushes_pending_message_before_timer_fires():
    """Caso 5519994108706 (28/07): a mensagem já está gravada em `messages`, mas o
    timer do debounce só existe em memória. Se o container reinicia dentro da janela,
    ninguém processa e o paciente nunca recebe resposta — falha 100% silenciosa.
    O drain do shutdown tem que despachar o que estiver pendente."""
    from app.buffer import push, drain

    handler = AsyncMock()
    # Debounce longo: sem o drain, o handler NUNCA seria chamado neste teste.
    with patch("app.buffer.DEBOUNCE_SECONDS", 30):
        await push(PHONE, "Oi", handler)
        handler.assert_not_awaited()
        flushed = await drain()

    assert flushed == 1
    handler.assert_awaited_once_with(PHONE, "Oi")


async def test_drain_coalesces_pending_messages_into_one_call():
    """O drain não pode quebrar a coalescência: o que está acumulado vira UM turno."""
    from app.buffer import push, drain

    handler = AsyncMock()
    with patch("app.buffer.DEBOUNCE_SECONDS", 30):
        await push(PHONE, "oi", handler)
        await push(PHONE, "quero marcar", handler)
        await drain()

    handler.assert_awaited_once()
    combined_text = handler.call_args[0][1]
    assert "oi" in combined_text
    assert "quero marcar" in combined_text


async def test_drain_cancels_timer_so_handler_runs_only_once():
    """Depois do drain o timer pendente não pode disparar um segundo turno."""
    from app.buffer import push, drain

    handler = AsyncMock()
    with patch("app.buffer.DEBOUNCE_SECONDS", 0.05):
        await push(PHONE, "Oi", handler)
        await drain()
        # Tempo de sobra para o call_later original disparar, se não tivesse sido cancelado
        await asyncio.sleep(0.2)

    handler.assert_awaited_once()


async def test_drain_with_nothing_pending_is_a_noop():
    from app.buffer import drain

    assert await drain() == 0


async def test_drain_drains_every_phone():
    from app.buffer import push, drain

    handler = AsyncMock()
    other = "5583988888888@s.whatsapp.net"
    with patch("app.buffer.DEBOUNCE_SECONDS", 30):
        await push(PHONE, "oi", handler)
        await push(other, "olá", handler)
        assert await drain() == 2

    assert handler.await_count == 2
    assert {c[0][0] for c in handler.call_args_list} == {PHONE, other}


async def test_drain_does_not_raise_when_handler_fails():
    """Uma falha no handler não pode derrubar o shutdown."""
    from app.buffer import push, drain

    handler = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.buffer.DEBOUNCE_SECONDS", 30):
        await push(PHONE, "Oi", handler)
        await drain()

    handler.assert_awaited_once()


async def test_drain_gives_up_after_timeout_instead_of_hanging_shutdown():
    """Se o handler travar, o drain respeita o timeout e devolve o controle."""
    from app.buffer import push, drain

    started = asyncio.Event()

    async def hanging_handler(_phone: str, _text: str) -> None:
        started.set()
        await asyncio.sleep(30)

    with patch("app.buffer.DEBOUNCE_SECONDS", 30):
        await push(PHONE, "Oi", hanging_handler)
        await asyncio.wait_for(drain(timeout=0.05), timeout=5)

    assert started.is_set()


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
