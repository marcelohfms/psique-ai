"""Testa o cron de relatório de funil (scripts/send_funnel_report.py) e o envio."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── send_report_email ─────────────────────────────────────────────────────────

async def test_send_report_email_uses_explicit_recipient(monkeypatch):
    import app.email_sender as es
    captured = {}

    def fake_send_email(host, port, user, password, to_email, subject, body, html_body=None):
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
    def order(self, *a, **k):
        return self
    def limit(self, *a, **k):
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
    async def fake_send(subject, body, to_email, html_body=None):
        sent["to"] = to_email
        sent["body"] = body
        sent["html"] = html_body

    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("FUNNEL_REPORT_EMAIL", "dona@example.com")

    await cron.run(client, NOW)

    assert sent["to"] == "dona@example.com"
    # aaa converteu (pagou), bbb é pendente qualificado
    assert "Conversão: 1 de 2" in sent["body"]
    assert "Bruno" in sent["body"] and "5581bbb" in sent["body"]
    assert sent["html"] is not None and "<" in sent["html"]
    assert "5581bbb" in sent["html"]      # pendente aparece no HTML


async def test_cron_raises_without_recipient(monkeypatch):
    client = _FakeClient({"events": [], "messages": []})
    monkeypatch.delenv("FUNNEL_REPORT_EMAIL", raising=False)
    with pytest.raises(EnvironmentError):
        await cron.run(client, NOW)


async def test_cron_never_uses_clinic_email(monkeypatch):
    client = _FakeClient({
        "events": [
            {"phone": "5581bbb", "event_type": "conversation_started",
             "created_at": (NOW - timedelta(days=1)).isoformat()},
        ],
        "messages": [],
    })

    async def fake_get_user(phone):
        return {}

    sent = {}
    async def fake_send(subject, body, to_email, html_body=None):
        sent["to"] = to_email

    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("FUNNEL_REPORT_EMAIL", "dona@example.com")
    monkeypatch.setenv("CLINIC_NOTIFY_EMAIL", "clinica@example.com")

    await cron.run(client, NOW)

    assert sent["to"] == "dona@example.com"
    assert sent["to"] != "clinica@example.com"


# ── _send_email com html + send_report_email passando html_body ────────────────

def _fake_smtp(captured):
    class _FakeServer:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, *a):
            pass
        def sendmail(self, frm, to, message):
            captured["msg"] = message
    return lambda *a, **k: _FakeServer()


def test_send_email_attaches_html_when_given(monkeypatch):
    import app.email_sender as es
    captured = {}
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", _fake_smtp(captured))
    es._send_email("h", 465, "u@e", "pw", "to@e", "assunto", "corpo texto",
                   html_body="<b>oi</b>")
    assert "text/plain" in captured["msg"]
    assert "text/html" in captured["msg"]


def test_send_email_plain_only_without_html(monkeypatch):
    import app.email_sender as es
    captured = {}
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", _fake_smtp(captured))
    es._send_email("h", 465, "u@e", "pw", "to@e", "assunto", "corpo texto")
    assert "text/plain" in captured["msg"]
    assert "text/html" not in captured["msg"]


async def test_send_report_email_forwards_html(monkeypatch):
    import app.email_sender as es
    captured = {}

    def fake_send_email(host, port, user, password, to_email, subject, body, html_body=None):
        captured["html_body"] = html_body

    monkeypatch.setattr(es, "_send_email", fake_send_email)
    with monkeypatch.context() as m:
        m.setenv("SMTP_HOST", "h"); m.setenv("SMTP_USER", "u@e"); m.setenv("SMTP_PASSWORD", "pw")
        await es.send_report_email("s", "corpo", "dona@e", html_body="<b>x</b>")

    assert captured["html_body"] == "<b>x</b>"
