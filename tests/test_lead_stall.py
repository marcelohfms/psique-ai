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
