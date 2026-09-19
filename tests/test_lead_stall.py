"""Testa a lógica pura de situação do lead (app/lead_stall.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.lead_stall import (
    classify_situation, select_recent_phones, label_ops, needs_label_change,
    LABEL_NEW, LABEL_CADASTRO, LABEL_AGENDAMENTO, LEAD_LABELS,
)

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


def _base(**over):
    facts = dict(
        active=True,
        has_appointment=False,
        offered_abandoned=False,
        registration_complete=False,
        has_name=True,
        last_msg_at=NOW - timedelta(minutes=30),
        now=NOW,
    )
    facts.update(over)
    return facts


# ── classify_situation ────────────────────────────────────────────────────────

def test_fresh_incomplete_registration_is_lead_novo():
    assert classify_situation(**_base()) == LABEL_NEW


def test_silent_with_name_is_cadastro_abandonado():
    assert classify_situation(**_base(last_msg_at=NOW - timedelta(hours=5))) == LABEL_CADASTRO


def test_silent_without_name_is_nothing():
    assert classify_situation(**_base(last_msg_at=NOW - timedelta(hours=5), has_name=False)) is None


def test_offered_abandoned_is_agendamento_abandonado():
    # cadastro completo + viu horários e não confirmou
    assert classify_situation(**_base(registration_complete=True, offered_abandoned=True)) == LABEL_AGENDAMENTO


def test_completed_registration_without_abandon_is_nothing():
    assert classify_situation(**_base(registration_complete=True)) is None


def test_has_appointment_is_nothing():
    assert classify_situation(**_base(has_appointment=True, offered_abandoned=True)) is None


def test_paused_is_nothing():
    assert classify_situation(**_base(active=False, last_msg_at=NOW - timedelta(hours=5))) is None


def test_is_lead_paused():
    from app.lead_stall import is_lead_paused
    assert is_lead_paused({"active": True}) is False
    assert is_lead_paused({}) is False
    assert is_lead_paused({"active": False}) is True
    assert is_lead_paused({"active": True, "manual_hold": True}) is True


# ── select_recent_phones ──────────────────────────────────────────────────────

def _msg(phone, role, dt):
    return {"phone": phone, "role": role, "created_at": dt.isoformat()}


def test_select_keeps_latest_user_message_within_window():
    rows = [
        _msg("5581111", "user", NOW - timedelta(days=1)),
        _msg("5581111", "user", NOW - timedelta(hours=2)),
        _msg("5582222", "assistant", NOW - timedelta(hours=1)),  # não conta (assistant)
        _msg("5583333", "user", NOW - timedelta(days=30)),        # fora da janela
    ]
    got = select_recent_phones(rows, NOW)
    assert set(got) == {"5581111"}
    assert got["5581111"] == NOW - timedelta(hours=2)


# ── label_ops / needs_label_change ────────────────────────────────────────────

def test_label_ops_sets_one_removes_others():
    add, remove = label_ops(LABEL_CADASTRO)
    assert add == [LABEL_CADASTRO]
    assert set(remove) == set(LEAD_LABELS) - {LABEL_CADASTRO}


def test_label_ops_none_removes_all():
    add, remove = label_ops(None)
    assert add == []
    assert set(remove) == set(LEAD_LABELS)


def test_needs_label_change():
    assert needs_label_change(LABEL_NEW, None) is True
    assert needs_label_change(LABEL_NEW, LABEL_NEW) is False
    assert needs_label_change(None, LABEL_NEW) is True
    assert needs_label_change(None, None) is False


# ── evaluate_leads (I/O mockado) ──────────────────────────────────────────────

class _FakeMessagesClient:
    """from_('messages').select(...).gte(...).execute() → rows fixos."""
    def __init__(self, rows):
        self._rows = rows

    def from_(self, table):
        assert table == "messages"
        return self

    def select(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    async def execute(self):
        from unittest.mock import MagicMock
        return MagicMock(data=self._rows)


async def test_evaluate_leads_classifies_each_phone(monkeypatch):
    import app.lead_stall as mod

    rows = [_msg("5581111", "user", NOW - timedelta(hours=5))]  # silêncio 5h, tem nome
    client = _FakeMessagesClient(rows)

    async def fake_fetch_abandoned(c, now, **k):
        return []  # ninguém abandonou agendamento

    async def fake_get_user(phone):
        return {"name": "Ana", "active": True}

    async def fake_upcoming(phone):
        return []

    monkeypatch.setattr(mod, "fetch_abandoned", fake_fetch_abandoned)
    monkeypatch.setattr("app.database.get_user_by_phone", fake_get_user)
    monkeypatch.setattr("app.database.get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr("app.database.is_registration_complete", lambda u: False)

    recs = await mod.evaluate_leads(client, NOW)
    assert len(recs) == 1
    assert recs[0]["phone"] == "5581111"
    assert recs[0]["situation"] == LABEL_CADASTRO
    assert recs[0]["user"]["name"] == "Ana"


async def test_manual_hold_via_evaluate_is_nothing(monkeypatch):
    import app.lead_stall as mod

    rows = [_msg("5581111", "user", NOW - timedelta(hours=5))]
    client = _FakeMessagesClient(rows)

    async def fake_fetch_abandoned(c, now, **k):
        return []

    async def fake_get_user(phone):
        return {"name": "Ana", "active": True, "manual_hold": True}

    async def fake_upcoming(phone):
        return []

    monkeypatch.setattr(mod, "fetch_abandoned", fake_fetch_abandoned)
    monkeypatch.setattr("app.database.get_user_by_phone", fake_get_user)
    monkeypatch.setattr("app.database.get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr("app.database.is_registration_complete", lambda u: False)

    recs = await mod.evaluate_leads(client, NOW)
    assert recs[0]["situation"] is None


async def test_evaluate_uses_exclude_handled_false(monkeypatch):
    import app.lead_stall as mod
    captured = {}

    rows = [_msg("5581111", "user", NOW - timedelta(minutes=10))]
    client = _FakeMessagesClient(rows)

    async def fake_fetch_abandoned(c, now, **k):
        captured["kwargs"] = k
        return []

    async def fake_get_user(phone):
        return {"name": "Ana", "active": True}

    async def fake_upcoming(phone):
        return []

    monkeypatch.setattr(mod, "fetch_abandoned", fake_fetch_abandoned)
    monkeypatch.setattr("app.database.get_user_by_phone", fake_get_user)
    monkeypatch.setattr("app.database.get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr("app.database.is_registration_complete", lambda u: False)

    await mod.evaluate_leads(client, NOW)
    assert captured["kwargs"].get("exclude_handled") is False
