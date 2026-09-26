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


# ── guarda: quem já tem consulta ativa não está abandonado ────────────────────
# Cenário real (Ana Daniela): paciente com consulta marcada e confirmada pede para
# adiantar o horário, a Eva mostra horários (slots_offered), ela mantém a consulta
# que já tinha. Sem novo appointment_booked/rescheduled, o detector antigo tratava
# como abandono e disparava o nudge "a gente não chegou a fechar o horário".

class _TableAwareQuery:
    """Fake de query que roteia por tabela e aplica in_/eq/gte em memória."""

    def __init__(self, rows):
        self._rows = rows
        self._filters = []
        self._event_key = None

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def eq(self, col, val):
        if col == "event_type":
            self._event_key = val
        else:
            self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        if col == "event_type":
            self._event_key = tuple(vals)
        else:
            self._filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    async def execute(self):
        rows = self._rows
        if self._event_key is not None:  # tabela events, keyed por event_type
            rows = rows.get(self._event_key, []) if isinstance(rows, dict) else []
        for kind, col, val in self._filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif kind == "in":
                rows = [r for r in rows if r.get(col) in val]
            elif kind == "gte":
                rows = [r for r in rows if str(r.get(col)) >= str(val)]
        return MagicMock(data=rows)


class _TableAwareClient:
    def __init__(self, events, contacts=None, appointments=None):
        self._events = events
        self._contacts = contacts or []
        self._appointments = appointments or []

    def from_(self, table):
        if table == "contacts":
            return _TableAwareQuery(self._contacts)
        if table == "appointments":
            return _TableAwareQuery(self._appointments)
        return _TableAwareQuery(self._events)


def _abandoning_events():
    offered = NOW - timedelta(hours=6)
    return {
        "slots_offered": [{"phone": "5581996993880", "metadata": {"doctor": "bruna"},
                           "created_at": offered.isoformat()}],
        CONVERSION_EVENTS: [],
        HANDLED_EVENTS: [],
    }


async def test_fetch_abandoned_skips_phone_with_active_appointment():
    """Telefone com consulta scheduled futura não entra na varredura de abandono."""
    client = _TableAwareClient(
        events=_abandoning_events(),
        contacts=[{"id": "c1", "phone": "5581996993880"}],
        appointments=[{"contact_id": "c1", "status": "scheduled",
                       "start_time": (NOW + timedelta(days=1)).isoformat()}],
    )
    assert await fetch_abandoned(client, NOW) == []


async def test_fetch_abandoned_flags_phone_without_active_appointment():
    """Sem consulta ativa, o abandono continua sendo detectado (não silencia demais)."""
    client = _TableAwareClient(
        events=_abandoning_events(),
        contacts=[{"id": "c1", "phone": "5581996993880"}],
        appointments=[],  # nenhuma consulta ativa
    )
    result = await fetch_abandoned(client, NOW)
    assert [c["phone"] for c in result] == ["5581996993880"]


async def test_fetch_abandoned_ignores_canceled_appointment():
    """Consulta cancelada não protege: continua sendo abandono."""
    client = _TableAwareClient(
        events=_abandoning_events(),
        contacts=[{"id": "c1", "phone": "5581996993880"}],
        appointments=[{"contact_id": "c1", "status": "canceled",
                       "start_time": (NOW + timedelta(days=1)).isoformat()}],
    )
    result = await fetch_abandoned(client, NOW)
    assert [c["phone"] for c in result] == ["5581996993880"]


# ── Consulta mantida que já começou (caso Patrícia/Maria José, 25/09/2026) ────
# A responsável pediu para remarcar na véspera, viu horários e decidiu manter a
# consulta. A guarda só olhava consultas com início a partir de AGORA; quando a
# consulta começou, a proteção caiu e o nudge saiu uma hora depois dela.

def _events_offered_at(offered: datetime):
    return {
        "slots_offered": [{"phone": "5581982131153", "metadata": {"doctor": "bruna"},
                           "created_at": offered.isoformat()}],
        CONVERSION_EVENTS: [],
        HANDLED_EVENTS: [],
    }


async def test_fetch_abandoned_skips_kept_appointment_already_started():
    offered = NOW - timedelta(hours=20)
    client = _TableAwareClient(
        events=_events_offered_at(offered),
        contacts=[{"id": "c1", "phone": "5581982131153"}],
        appointments=[{"contact_id": "c1", "status": "scheduled",
                       "start_time": (NOW - timedelta(hours=1)).isoformat()}],
    )
    assert await fetch_abandoned(client, NOW) == []


async def test_fetch_abandoned_skips_kept_appointment_already_completed():
    offered = NOW - timedelta(hours=20)
    client = _TableAwareClient(
        events=_events_offered_at(offered),
        contacts=[{"id": "c1", "phone": "5581982131153"}],
        appointments=[{"contact_id": "c1", "status": "completed",
                       "start_time": (NOW - timedelta(hours=1)).isoformat()}],
    )
    assert await fetch_abandoned(client, NOW) == []


async def test_fetch_abandoned_old_appointment_before_offer_does_not_protect():
    """Consulta que aconteceu ANTES da oferta não conta: a pessoa voltou a pedir
    horário e parou, é abandono de verdade."""
    offered = NOW - timedelta(hours=20)
    client = _TableAwareClient(
        events=_events_offered_at(offered),
        contacts=[{"id": "c1", "phone": "5581982131153"}],
        appointments=[{"contact_id": "c1", "status": "completed",
                       "start_time": (offered - timedelta(days=30)).isoformat()}],
    )
    result = await fetch_abandoned(client, NOW)
    assert [c["phone"] for c in result] == ["5581982131153"]
