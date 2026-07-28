import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Awaitable

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


@dataclass
class _Entry:
    messages: list[str] = field(default_factory=list)
    handle: asyncio.TimerHandle | None = None
    # Monotonic timestamp of the first deferral caused by an in-flight invoke.
    deferred_since: float | None = None


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

    if entry.handle is not None:
        entry.handle.cancel()

    loop = asyncio.get_running_loop()

    def _fire() -> None:
        # An invoke is still running for this phone: keep accumulating instead of
        # dispatching a second turn that would just queue on the phone lock.
        if get_phone_lock(phone).locked():
            now = loop.time()
            if entry.deferred_since is None:
                entry.deferred_since = now
            if now - entry.deferred_since < _MAX_DEFER_SECONDS:
                entry.handle = loop.call_later(_DEFER_RETRY_SECONDS, _fire)
                return
            logger.warning(
                "Buffer deferral cap (%.0fs) reached for %s while an invoke is "
                "still in flight — dispatching anyway",
                _MAX_DEFER_SECONDS, phone,
            )

        combined = " ".join(entry.messages)
        entry.messages.clear()
        entry.handle = None
        entry.deferred_since = None
        asyncio.create_task(_run(phone, combined, handler))

    entry.handle = loop.call_later(DEBOUNCE_SECONDS, _fire)


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
