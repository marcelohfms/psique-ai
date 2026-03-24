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
    task: asyncio.Task | None = None


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
    """
    entry = _pending[phone]
    entry.messages.append(text)

    # Cancel previous scheduled flush
    if entry.task and not entry.task.done():
        entry.task.cancel()

    async def flush():
        await asyncio.sleep(DEBOUNCE_SECONDS)
        combined = " ".join(entry.messages)
        entry.messages.clear()
        entry.task = None
        logger.debug("Buffer flush for %s: %r", phone, combined)
        try:
            await handler(phone, combined)
        except Exception:
            logger.exception("Error in buffer flush for %s", phone)

    entry.task = asyncio.create_task(flush())
