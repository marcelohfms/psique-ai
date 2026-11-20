"""Testa o cron diário de falhas de leitura de comprovante
(scripts/send_daily_receipt_failure_report.py) e o envio."""
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

import pytest

import scripts.send_daily_receipt_failure_report as cron
from app.receipt_reconciliation import RECEIPT_DROP_EVENT, REPLAY_EVENT

TZ = ZoneInfo("America/Recife")
NOW = datetime(2026, 11, 19, 8, 0, tzinfo=TZ)  # roda 8h; cobre o dia 18
YESTERDAY = "2026-11-18T18:36:00+00:00"


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *a, **k):
        return self
    def in_(self, *a, **k):
        return self
    def gte(self, *a, **k):
        return self
    def lt(self, *a, **k):
        return self
    def order(self, *a, **k):
        return self
    async def execute(self):
        return MagicMock(data=self._rows)


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
    def from_(self, table):
        return _FakeQuery(self._rows)


async def test_cron_builds_and_sends_to_owner(monkeypatch):
    rows = [
        {"phone": "353833450100", "event_type": REPLAY_EVENT,
         "created_at": YESTERDAY, "metadata": {"attachment_id": 2229}},
        {"phone": "5581999736464", "event_type": RECEIPT_DROP_EVENT,
         "created_at": "2026-11-18T12:00:00+00:00", "metadata": {"attachment_id": 10}},
    ]
    client = _FakeClient(rows)

    async def fake_get_user(phone):
        return {"patient_name": "Júlia"} if phone == "353833450100" else {"patient_name": "Fulana"}

    sent = {}
    async def fake_send(subject, body, to_email, html_body=None):
        sent.update(subject=subject, body=body, to=to_email, html=html_body)

    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("RECEIPT_FAILURE_REPORT_EMAIL", "dona@example.com")

    await cron.run(client, NOW)

    assert sent["to"] == "dona@example.com"
    assert "Júlia" in sent["body"]
    assert "18/11/2026" in sent["body"]
    assert sent["html"] is not None and "<" in sent["html"]
    # anexo 10 caiu e não foi resgatado → precisa de atenção
    assert "PRECISA DE ATENÇÃO" in sent["body"]


async def test_cron_excludes_affectus_from_the_report(monkeypatch):
    rows = [
        {"phone": "558191812399", "event_type": RECEIPT_DROP_EVENT,
         "created_at": YESTERDAY, "metadata": {"attachment_id": 999}},
    ]
    client = _FakeClient(rows)

    async def fake_get_user(phone):
        return {"patient_name": "Clínica Affectus"}

    sent = {}
    async def fake_send(subject, body, to_email, html_body=None):
        sent.update(body=body, to=to_email)

    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("RECEIPT_FAILURE_REPORT_EMAIL", "dona@example.com")

    await cron.run(client, NOW)

    assert "Affectus" not in sent["body"]
    assert "Nenhuma falha" in sent["body"]


async def test_cron_raises_without_recipient(monkeypatch):
    monkeypatch.delenv("RECEIPT_FAILURE_REPORT_EMAIL", raising=False)
    with pytest.raises(EnvironmentError):
        await cron.run(_FakeClient([]), NOW)


async def test_cron_never_uses_clinic_email(monkeypatch):
    client = _FakeClient([])
    sent = {}
    async def fake_send(subject, body, to_email, html_body=None):
        sent["to"] = to_email
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("RECEIPT_FAILURE_REPORT_EMAIL", "dona@example.com")
    monkeypatch.setenv("CLINIC_NOTIFY_EMAIL", "clinica@example.com")

    await cron.run(client, NOW)

    assert sent["to"] == "dona@example.com"
