"""Testa o cron de relatório de funil (scripts/send_funnel_report.py) e o envio."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── send_report_email ─────────────────────────────────────────────────────────

async def test_send_report_email_uses_explicit_recipient(monkeypatch):
    import app.email_sender as es
    captured = {}

    def fake_send_email(host, port, user, password, to_email, subject, body):
        captured["to"] = to_email
        captured["subject"] = subject

    monkeypatch.setattr(es, "_send_email", fake_send_email)
    with monkeypatch.context() as m:
        m.setenv("SMTP_HOST", "smtp.example.com")
        m.setenv("SMTP_USER", "u@example.com")
        m.setenv("SMTP_PASSWORD", "pw")
        await es.send_report_email("assunto", "corpo", "dona@example.com")

    assert captured["to"] == "dona@example.com"
    assert captured["subject"] == "assunto"


async def test_send_report_email_raises_without_recipient(monkeypatch):
    import app.email_sender as es
    with monkeypatch.context() as m:
        m.setenv("SMTP_HOST", "smtp.example.com")
        m.setenv("SMTP_USER", "u@example.com")
        m.setenv("SMTP_PASSWORD", "pw")
        with pytest.raises(RuntimeError):
            await es.send_report_email("assunto", "corpo", "")

# ── cron: montagem dos conjuntos e envio ──────────────────────────────────────

import scripts.send_funnel_report as cron


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *a, **k):
        return self
    def in_(self, *a, **k):
        return self
    def gte(self, *a, **k):
        return self
    async def execute(self):
        return MagicMock(data=self._rows)


class _FakeClient:
    def __init__(self, by_table):
        self._by_table = by_table
    def from_(self, table):
        return _FakeQuery(self._by_table.get(table, []))


async def test_cron_builds_sets_and_sends(monkeypatch):
    events = [
        {"phone": "5581aaa", "event_type": "conversation_started", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581aaa", "event_type": "slots_offered", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581aaa", "event_type": "appointment_booked", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581aaa", "event_type": "payment_receipt_registered", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581bbb", "event_type": "conversation_started", "created_at": (NOW - timedelta(days=1)).isoformat()},
        {"phone": "5581bbb", "event_type": "slots_offered", "created_at": (NOW - timedelta(days=1)).isoformat()},
    ]
    messages = [
        {"phone": "5581bbb", "role": "user", "created_at": (NOW - timedelta(days=1)).isoformat()},
    ]
    client = _FakeClient({"events": events, "messages": messages})

    async def fake_get_user(phone):
        return {"name": "Bruno"} if phone == "5581bbb" else {}

    sent = {}
    async def fake_send(subject, body, to_email):
        sent["to"] = to_email
        sent["body"] = body

    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("FUNNEL_REPORT_EMAIL", "dona@example.com")

    await cron.run(client, NOW)

    assert sent["to"] == "dona@example.com"
    # aaa converteu (pagou), bbb é pendente qualificado
    assert "Conversão: 1 de 2" in sent["body"]
    assert "Bruno" in sent["body"] and "5581bbb" in sent["body"]


async def test_cron_raises_without_recipient(monkeypatch):
    client = _FakeClient({"events": [], "messages": []})
    monkeypatch.delenv("FUNNEL_REPORT_EMAIL", raising=False)
    with pytest.raises(EnvironmentError):
        await cron.run(client, NOW)
