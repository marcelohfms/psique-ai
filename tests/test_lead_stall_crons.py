"""Testa o cron de situação do lead (scripts/send_lead_stall_nudges.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import scripts.send_lead_stall_nudges as cron
from app.lead_stall import LABEL_NEW, LABEL_CADASTRO, LABEL_AGENDAMENTO

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


# ── reconciliação de label ────────────────────────────────────────────────────

async def test_reconcile_writes_when_label_changed(monkeypatch):
    calls = {}

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nunca setamos label antes

    async def fake_find_or_create(phone):
        return 4242

    async def fake_set_labels(conv_id, add, remove):
        calls["conv_id"] = conv_id
        calls["add"] = add
        calls["remove"] = remove

    async def fake_log_event(event_type, phone, metadata=None):
        calls["logged"] = (event_type, metadata)

    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "get_conversation_id", lambda p: None)
    monkeypatch.setattr(cron, "find_or_create_conversation", fake_find_or_create)
    monkeypatch.setattr(cron, "set_labels", fake_set_labels)
    monkeypatch.setattr(cron, "log_event", fake_log_event)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO, "user": {}, "last_msg_at": NOW}
    await cron._reconcile_label(rec)

    assert calls["conv_id"] == 4242
    assert calls["add"] == [LABEL_CADASTRO]
    assert LABEL_NEW in calls["remove"] and LABEL_AGENDAMENTO in calls["remove"]
    assert calls["logged"][0] == "lead_label_set"


async def test_reconcile_skips_when_paused(monkeypatch):
    from unittest.mock import AsyncMock
    set_labels_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", AsyncMock(return_value=[{"metadata": {"label": LABEL_CADASTRO}}]))
    monkeypatch.setattr(cron, "set_labels", set_labels_mock)

    rec = {"phone": "5581111", "situation": None, "user": {"active": False}, "last_msg_at": NOW}
    await cron._reconcile_label(rec)

    set_labels_mock.assert_not_awaited()


async def test_reconcile_skips_when_label_unchanged(monkeypatch):
    async def fake_get_events(phone, event_type, limit=50):
        return [{"metadata": {"label": LABEL_CADASTRO}}]

    set_labels_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "set_labels", set_labels_mock)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO, "user": {}, "last_msg_at": NOW}
    await cron._reconcile_label(rec)

    set_labels_mock.assert_not_awaited()


# ── nudge ─────────────────────────────────────────────────────────────────────

async def test_nudge_sends_for_cadastro_abandonado_active_in_window(monkeypatch):
    sent = {}

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nunca cutucado

    async def fake_window(client, phone, now):
        return True

    async def fake_send(phone, text):
        sent["phone"] = phone
        sent["text"] = text

    async def fake_log_event(event_type, phone, metadata=None):
        sent["logged"] = event_type

    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "_window_open", fake_window)
    monkeypatch.setattr(cron, "send_whatsapp", fake_send)
    monkeypatch.setattr(cron, "log_event", fake_log_event)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO,
           "user": {"name": "Ana", "active": True}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    assert sent["phone"] == "5581111"
    assert "Ana" in sent["text"]
    assert sent["logged"] == "lead_stall_nudge_sent"


async def test_nudge_skipped_when_already_nudged(monkeypatch):
    async def fake_get_events(phone, event_type, limit=50):
        return [{"metadata": {}}]  # já cutucado

    send_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "send_whatsapp", send_mock)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO,
           "user": {"name": "Ana", "active": True}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    send_mock.assert_not_awaited()


async def test_nudge_skipped_when_paused(monkeypatch):
    async def fake_get_events(phone, event_type, limit=50):
        return []

    send_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "send_whatsapp", send_mock)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO,
           "user": {"name": "Ana", "active": False}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    send_mock.assert_not_awaited()


async def test_nudge_skipped_when_not_cadastro(monkeypatch):
    send_mock = AsyncMock()
    monkeypatch.setattr(cron, "send_whatsapp", send_mock)

    rec = {"phone": "5581111", "situation": LABEL_NEW,
           "user": {"name": "Ana", "active": True}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    send_mock.assert_not_awaited()


# ── relatório: seleção de cadastro-abandonado frio ────────────────────────────

import scripts.send_scheduling_stall_report as report
from app.lead_stall import REPORT_EVENT


async def test_report_selects_cold_cadastro_abandonado(monkeypatch):
    async def fake_evaluate(client, now):
        return [
            {"phone": "5581111", "situation": LABEL_CADASTRO,
             "user": {"name": "Ana", "active": False}, "last_msg_at": NOW},   # pausado → reportável
            {"phone": "5582222", "situation": LABEL_CADASTRO,
             "user": {"name": "Bia", "active": True}, "last_msg_at": NOW},    # ativo+janela → nudge cuida
            {"phone": "5583333", "situation": LABEL_NEW,
             "user": {"name": "Cid", "active": True}, "last_msg_at": NOW},    # não é cadastro-abandonado
        ]

    async def fake_window(client, phone, now):
        return True  # 5582222 está na janela

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nada reportado ainda

    monkeypatch.setattr(report, "evaluate_leads", fake_evaluate)
    monkeypatch.setattr(report, "_window_open_safe", fake_window)
    monkeypatch.setattr(report, "get_events_by_type", fake_get_events)

    got = await report.fetch_cadastro_abandonado_reportable(client=None, now=NOW)
    assert [c["phone"] for c in got] == ["5581111"]
    assert got[0]["name"] == "Ana"
