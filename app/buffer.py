import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "3"))


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
    entry.messages.append(text)

    if entry.handle is not None:
        entry.handle.cancel()

    loop = asyncio.get_running_loop()

    def _fire() -> None:
        combined = " ".join(entry.messages)
        entry.messages.clear()
        entry.handle = None
        print(f"BUFFER: firing for {phone}: {combined!r}", flush=True)
        asyncio.create_task(_run(phone, combined, handler))

    entry.handle = loop.call_later(DEBOUNCE_SECONDS, _fire)


async def _run(
    phone: str,
    combined: str,
    handler: Callable[[str, str], Awaitable[None]],
) -> None:
    try:
        await handler(phone, combined)
    except Exception:
        logger.exception("Error in buffer flush for %s", phone)
