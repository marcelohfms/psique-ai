"""Testa a lógica compartilhada de agendamento abandonado (app/scheduling_stall.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.scheduling_stall import (
    select_abandoned, fetch_abandoned, is_nudge_eligible,
    CONVERSION_EVENTS, HANDLED_EVENTS,
)

NOW = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=4)


def _offer(dt: datetime, **md) -> dict:
    return {"created_at": dt.isoformat(), "metadata": md}


# ── select_abandoned ──────────────────────────────────────────────────────────

def test_offer_older_than_cutoff_without_booking_is_abandoned():
    latest = {"5583111": _offer(NOW - timedelta(hours=5), doctor="julio")}
    result = select_abandoned(latest, {}, CUTOFF)
    assert [c["phone"] for c in result] == ["5583111"]
    assert result[0]["metadata"]["doctor"] == "julio"


def test_offer_within_window_is_not_abandoned():
    latest = {"5583111": _offer(NOW - timedelta(hours=1))}
    assert select_abandoned(latest, {}, CUTOFF) == []


def test_booking_after_offer_removes_case():
    offered = NOW - timedelta(hours=5)
    latest = {"5583111": _offer(offered)}
    booked = {"5583111": [offered + timedelta(minutes=10)]}
    assert select_abandoned(latest, booked, CUTOFF) == []


def test_booking_before_offer_still_abandoned():
    offered = NOW - timedelta(hours=5)
    latest = {"5583111": _offer(offered)}
    booked = {"5583111": [offered - timedelta(days=30)]}
    assert [c["phone"] for c in select_abandoned(latest, booked, CUTOFF)] == ["5583111"]


def test_handled_phone_is_excluded():
    latest = {"5583111": _offer(NOW - timedelta(hours=5))}
    assert select_abandoned(latest, {}, CUTOFF, handled={"5583111"}) == []


def test_multiple_phones_sorted_by_offer_time():
    latest = {
        "A": _offer(NOW - timedelta(hours=5)),
        "B": _offer(NOW - timedelta(hours=8)),
        "C": _offer(NOW - timedelta(hours=1)),   # dentro da janela → excluído
    }
    assert [c["phone"] for c in select_abandoned(latest, {}, CUTOFF)] == ["B", "A"]


# ── is_nudge_eligible ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("active,window,expected", [
    (True, True, True),      # ativo + janela aberta → Eva cutuca
    (True, False, False),    # frio (fora das 24h) → e-mail clínica
    (False, True, False),    # eva-inativa/pausado → e-mail clínica
    (False, False, False),
])
def test_is_nudge_eligible(active, window, expected):
    assert is_nudge_eligible(active, window) is expected


# ── fetch_abandoned (client mockado) ──────────────────────────────────────────

class _FakeQuery:
    def __init__(self, data_map):
        self._data_map = data_map
        self._key = None

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def eq(self, col, val):
        if col == "event_type":
            self._key = val
        return self

    def in_(self, col, vals):
        if col == "event_type":
            self._key = tuple(vals)
        return self

    async def execute(self):
        return MagicMock(data=self._data_map.get(self._key, []))


class _FakeClient:
    def __init__(self, data_map):
        self._data_map = data_map

    def from_(self, table):
        return _FakeQuery(self._data_map)


async def test_fetch_abandoned_returns_unbooked_offer():
    data = {
        "slots_offered": [{"phone": "5583111", "metadata": {"doctor": "bruna"},
                           "created_at": (NOW - timedelta(hours=6)).isoformat()}],
        CONVERSION_EVENTS: [],
        HANDLED_EVENTS: [],
    }
    result = await fetch_abandoned(_FakeClient(data), NOW)
    assert [c["phone"] for c in result] == ["5583111"]
    assert result[0]["metadata"]["doctor"] == "bruna"


async def test_fetch_abandoned_excludes_booked_after_offer():
    offered = NOW - timedelta(hours=6)
    data = {
        "slots_offered": [{"phone": "5583111", "metadata": {},
                           "created_at": offered.isoformat()}],
        CONVERSION_EVENTS: [{"phone": "5583111",
                             "created_at": (offered + timedelta(hours=1)).isoformat()}],
        HANDLED_EVENTS: [],
    }
    assert await fetch_abandoned(_FakeClient(data), NOW) == []


async def test_fetch_abandoned_excludes_already_handled():
    offered = NOW - timedelta(hours=6)
    data = {
        "slots_offered": [{"phone": "5583111", "metadata": {},
                           "created_at": offered.isoformat()}],
        CONVERSION_EVENTS: [],
        HANDLED_EVENTS: [{"phone": "5583111"}],
    }
    assert await fetch_abandoned(_FakeClient(data), NOW) == []
    # com exclude_handled=False, aparece (relatório read-only)
    got = await fetch_abandoned(_FakeClient(data), NOW, exclude_handled=False)
    assert [c["phone"] for c in got] == ["5583111"]
