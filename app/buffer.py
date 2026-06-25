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
        combined = " ".join(entry.messages)
        entry.messages.clear()
        entry.handle = None
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
