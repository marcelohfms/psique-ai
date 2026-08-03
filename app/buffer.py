import asyncio
import logging
import os
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Iterator

import openai

logger = logging.getLogger(__name__)

# How long to wait before re-trying a message that hit the OpenAI rate limit.
# The TPM quota window is 1 minute, so 65 s ensures the window has reset.
_RATE_LIMIT_RETRY_SECONDS = float(os.getenv("RATE_LIMIT_RETRY_SECONDS", "65"))

DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "3"))

# When the debounce window expires but an ainvoke is still running for the same
# phone, the flush is deferred instead of dispatched: the pending messages keep
# accumulating so everything that arrived *during* the invoke lands in the SAME
# turn. Without this, each message that arrives mid-invoke queues on the phone
# lock and produces its own reply (patient sends "Pix enviado" / "Saldo restante
# pago" on separate lines → Eva answers each one).
_DEFER_RETRY_SECONDS = float(os.getenv("BUFFER_DEFER_RETRY_SECONDS", "1"))

# Safety cap: if the in-flight invoke never releases the lock (hang, crash inside
# the graph), dispatch anyway rather than swallowing the message in silence.
_MAX_DEFER_SECONDS = float(os.getenv("BUFFER_MAX_DEFER_SECONDS", "60"))

# Per-phone lock: ensures only one graph.ainvoke() runs at a time per phone.
# Prevents race conditions when an attendant note and patient reply are processed
# concurrently (e.g. patient replies while silent-mode ainvoke is still running).
_phone_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_phone_lock(phone: str) -> asyncio.Lock:
    return _phone_locks[phone]


# Uma imagem/PDF só vira texto depois de passar pelo OpenAI Vision, o que leva
# 7-10 s. Nesse intervalo a mensagem é invisível para o buffer: um texto enviado
# logo antes dela tem o debounce expirado e dispara um turno SEM o comprovante —
# a Eva então cobra uma taxa que a paciente acabou de pagar (caso 5581991320003,
# 03/08/2026, idêntico ao de 09/07/2026: "Paciente Bernardo…" + comprovante 2 s
# depois → duas respostas, a primeira cobrando de novo).
#
# O hold é registrado assim que o webhook da mídia chega — antes da leitura — e
# só é solto depois que o texto transcrito entrou no buffer. Enquanto ele existe,
# `_fire` adia o despacho exatamente como faz com o lock do telefone, então o
# texto anterior e o comprovante caem no mesmo turno.
_holds: dict[str, int] = {}


def is_held(phone: str) -> bool:
    return _holds.get(phone, 0) > 0


@contextmanager
def hold(phone: str) -> Iterator[None]:
    """Segura o despacho do buffer para `phone` enquanto uma mídia é processada.

    Reentrante: duas mídias em voo ao mesmo tempo só liberam o buffer quando a
    última terminar. O teto de `_MAX_DEFER_SECONDS` em `_fire` continua valendo,
    então um hold que vaze (leitura travada) não prende a mensagem para sempre.
    """
    _holds[phone] = _holds.get(phone, 0) + 1
    try:
        yield
    finally:
        remaining = _holds.get(phone, 0) - 1
        if remaining > 0:
            _holds[phone] = remaining
        else:
            _holds.pop(phone, None)


# How long the shutdown drain waits for the flushed handlers to finish before
# giving up. Easypanel sends SIGTERM and waits ~10 s before SIGKILL, so this has
# to stay comfortably under that or the drain gets killed mid-flight anyway.
_DRAIN_TIMEOUT_SECONDS = float(os.getenv("BUFFER_DRAIN_TIMEOUT_SECONDS", "8"))


@dataclass
class _Entry:
    messages: list[str] = field(default_factory=list)
    handle: asyncio.TimerHandle | None = None
    # Monotonic timestamp of the first deferral caused by an in-flight invoke.
    deferred_since: float | None = None
    # Handler for the buffered messages, kept so `drain` can flush them on shutdown.
    handler: Callable[[str, str], Awaitable[None]] | None = None


# phone → pending entry
_pending: dict[str, _Entry] = defaultdict(_Entry)


async def push(
    phone: str,
    text: str,
    handler: Callable[[str, str], Awaitable[None]],
) -> None:
    """
    Buffer a message for `phone`. If no new message arrives within
    DEBOUNCE_SECONDS, calls handler(phone, combined_text).

    Uses loop.call_later instead of create_task so the actual handler
    runs in a task spawned from the event loop's root context, avoiding
    the 'ContextVar created in a different Context' error from LangGraph.
    """
    entry = _pending[phone]
    # Deduplicate: same text may arrive from both Meta and Chatwoot webhooks
    # for the same message. Don't add it twice within the same debounce window.
    if text not in entry.messages:
        entry.messages.append(text)
    entry.handler = handler

    if entry.handle is not None:
        entry.handle.cancel()

    loop = asyncio.get_running_loop()

    def _fire() -> None:
        # Um invoke ainda rodando (não adianta despachar: ficaria na fila do lock)
        # ou uma mídia ainda sendo lida (o texto dela ainda não existe): em ambos
        # os casos, continue acumulando em vez de abrir um segundo turno.
        blocked = get_phone_lock(phone).locked() or is_held(phone)
        if blocked:
            now = loop.time()
            if entry.deferred_since is None:
                entry.deferred_since = now
            if now - entry.deferred_since < _MAX_DEFER_SECONDS:
                entry.handle = loop.call_later(_DEFER_RETRY_SECONDS, _fire)
                return
            logger.warning(
                "Buffer deferral cap (%.0fs) reached for %s (invoke em voo ou "
                "mídia em leitura) — dispatching anyway",
                _MAX_DEFER_SECONDS, phone,
            )

        combined = " ".join(entry.messages)
        entry.messages.clear()
        entry.handle = None
        entry.deferred_since = None
        asyncio.create_task(_run(phone, combined, handler))

    entry.handle = loop.call_later(DEBOUNCE_SECONDS, _fire)


async def drain(timeout: float | None = None) -> int:
    """Flush every pending debounce entry immediately. Returns how many were flushed.

    The debounce timer lives only in memory (`loop.call_later`), but the patient's
    message is already persisted in `messages` by the time it reaches the buffer.
    A container restart inside the debounce window therefore loses the message with
    no error, no exception and no event — the patient simply never gets an answer
    (caso 5519994108706, 28/07/2026). Easypanel sends SIGTERM before SIGKILL, so on
    shutdown we dispatch whatever is still buffered instead of dropping it.

    Bounded by `timeout`: a hung handler must not block shutdown until SIGKILL,
    which would defeat the purpose. Handlers that don't finish in time are lost —
    the startup recovery in app/main.py is the backstop for those.
    """
    if timeout is None:
        timeout = _DRAIN_TIMEOUT_SECONDS

    # Snapshot first: _run may push again while we're iterating.
    pending = [
        (phone, entry)
        for phone, entry in list(_pending.items())
        if entry.messages and entry.handler is not None
    ]
    if not pending:
        return 0

    tasks = []
    for phone, entry in pending:
        if entry.handle is not None:
            entry.handle.cancel()
            entry.handle = None
        combined = " ".join(entry.messages)
        entry.messages.clear()
        entry.deferred_since = None
        logger.warning(
            "BUFFER_DRAIN flushing buffered message for %s on shutdown: %.60s",
            phone, combined,
        )
        tasks.append(asyncio.create_task(_run(phone, combined, entry.handler)))

    _done, still_running = await asyncio.wait(tasks, timeout=timeout)
    if still_running:
        logger.error(
            "BUFFER_DRAIN timed out after %.0fs with %d handler(s) unfinished — "
            "those messages depend on the startup recovery to be reprocessed",
            timeout, len(still_running),
        )
    return len(tasks)


async def _run(
    phone: str,
    combined: str,
    handler: Callable[[str, str], Awaitable[None]],
    _attempt: int = 1,
) -> None:
    try:
        await handler(phone, combined)
    except openai.RateLimitError as exc:
        if _attempt <= 3:
            logger.warning(
                "Rate limit hit for %s (attempt %d/3) — retrying in %.0fs: %s",
                phone, _attempt, _rate_limit_wait(_attempt), exc,
            )
            loop = asyncio.get_running_loop()
            delay = _rate_limit_wait(_attempt)
            loop.call_later(
                delay,
                lambda: asyncio.create_task(
                    _run(phone, combined, handler, _attempt + 1)
                ),
            )
        else:
            logger.error(
                "Rate limit retry exhausted for %s after %d attempts — message dropped",
                phone, _attempt,
            )
    except Exception:
        logger.exception("Error in buffer flush for %s", phone)


def _rate_limit_wait(attempt: int) -> float:
    """Backoff: 65 s, 90 s, 120 s for attempts 1-3."""
    return [65.0, 90.0, 120.0][min(attempt - 1, 2)]
