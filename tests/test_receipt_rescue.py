"""Tests for app/receipt_rescue.py — leitura do comprovante na chegada."""
import httpx
import pytest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio

KW = dict(phone="5581999348383", conversation_id=542, attachment_id=2242,
          data_url="http://x/2242", created_at=1795092787)


def _env(*, download=None, describe="[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100",
         trace=False, pay_trace=False, replayed=False):
    stack = ExitStack()
    m = {}
    m["download"] = stack.enter_context(patch(
        "app.receipt_rescue._download_bytes", new_callable=AsyncMock,
        side_effect=download if download is not None else [b"img"]))
    m["describe"] = stack.enter_context(patch(
        "app.receipt_rescue.describe_image_bytes", new_callable=AsyncMock, return_value=describe))
    m["log_event"] = stack.enter_context(patch("app.receipt_rescue.log_event", new_callable=AsyncMock))
    m["has_trace"] = stack.enter_context(patch(
        "app.receipt_rescue.has_trace", new_callable=AsyncMock, return_value=trace))
    m["pay_trace"] = stack.enter_context(patch(
        "app.receipt_rescue.has_payment_trace_until_now", new_callable=AsyncMock, return_value=pay_trace))
    m["replayed"] = stack.enter_context(patch(
        "app.receipt_rescue.already_replayed", new_callable=AsyncMock, return_value=replayed))
    m["save"] = stack.enter_context(patch("app.main.save_message", new_callable=AsyncMock))
    m["process"] = stack.enter_context(patch("app.main.process_message", new_callable=AsyncMock))
    return stack, m


async def test_retries_download_then_ingests():
    from app.receipt_rescue import RESCUED_EVENT, rescue_incoming_attachment
    from app.receipt_reconciliation import REPLAY_EVENT
    stack, m = _env(download=[httpx.ConnectError("boom"), b"img"])
    with stack:
        ok = await rescue_incoming_attachment(**KW, sleep=AsyncMock())
    assert ok is True
    assert m["download"].await_count == 2
    m["save"].assert_awaited_once()
    _, kwargs = m["process"].await_args
    assert kwargs.get("bypass_pause", False) is False
    logged = {c.args[0] for c in m["log_event"].await_args_list}
    assert REPLAY_EVENT in logged and RESCUED_EVENT in logged


async def test_skips_when_already_traced():
    from app.receipt_rescue import rescue_incoming_attachment
    stack, m = _env(trace=True)
    with stack:
        ok = await rescue_incoming_attachment(**KW, sleep=AsyncMock())
    assert ok is False
    m["download"].assert_not_called()
    m["process"].assert_not_awaited()


async def test_skips_when_already_replayed():
    from app.receipt_rescue import rescue_incoming_attachment
    stack, m = _env(replayed=True)
    with stack:
        ok = await rescue_incoming_attachment(**KW, sleep=AsyncMock())
    assert ok is False
    m["download"].assert_not_called()
    m["process"].assert_not_awaited()


async def test_gives_up_after_max_attempts():
    from app.receipt_rescue import RESCUE_DOWNLOAD_DELAYS, rescue_incoming_attachment
    stack, m = _env(download=httpx.ConnectError("boom"))
    with stack:
        ok = await rescue_incoming_attachment(**KW, sleep=AsyncMock())
    assert ok is False
    assert m["download"].await_count == len(RESCUE_DOWNLOAD_DELAYS)
    m["process"].assert_not_awaited()


async def test_skips_when_describe_returns_none():
    from app.receipt_rescue import rescue_incoming_attachment
    stack, m = _env(describe=None)
    with stack:
        ok = await rescue_incoming_attachment(**KW, sleep=AsyncMock())
    assert ok is False
    m["save"].assert_not_awaited()
    m["process"].assert_not_awaited()


async def test_skips_when_traced_after_download():
    from app.receipt_rescue import rescue_incoming_attachment
    stack, m = _env()
    m["has_trace"].side_effect = [False, True]
    with stack:
        ok = await rescue_incoming_attachment(**KW, sleep=AsyncMock())
    assert ok is False
    m["describe"].assert_not_awaited()
    m["process"].assert_not_awaited()
