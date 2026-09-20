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
