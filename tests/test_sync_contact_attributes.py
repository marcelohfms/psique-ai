"""Testa o cron de custom attributes de contato (scripts/sync_contact_attributes.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import scripts.sync_contact_attributes as cron

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── candidate_phones (client fake por tabela) ─────────────────────────────────

class _FakeQuery:
    def __init__(self, table, rows):
        self._table = table
        self._rows = rows
        self._min_created = None
    def select(self, *a, **k):
        return self
    def eq(self, *a, **k):
        return self
    def gte(self, col=None, val=None, *a, **k):
        if col == "created_at" and val is not None:
            self._min_created = val
        return self
    def in_(self, *a, **k):
        return self
    async def execute(self):
        rows = self._rows
        if self._min_created is not None:
            rows = [r for r in rows if r.get("created_at", "") >= self._min_created]
        return MagicMock(data=rows)


class _FakeClient:
    def __init__(self, by_table):
        self._by_table = by_table
    def from_(self, table):
        return _FakeQuery(table, self._by_table.get(table, []))


async def test_candidate_phones_unions_appointments_and_synced():
    client = _FakeClient({
        "appointments": [{"patient_id": "p1"}, {"patient_id": "p1"}],
        "patient_contacts": [{"contact_id": "c1"}],
        "contacts": [{"phone": "5581111"}],
        "events": [{"phone": "5582222", "created_at": NOW.isoformat()}],   # já sincronizado antes (recente)
    })
    got = await cron.candidate_phones(client, NOW)
    assert got == {"5581111", "5582222"}


async def test_candidate_phones_excludes_old_synced():
    from datetime import timedelta
    client = _FakeClient({
        "appointments": [],
        "events": [
            {"phone": "5581old", "created_at": (NOW - timedelta(days=60)).isoformat()},
            {"phone": "5582recent", "created_at": (NOW - timedelta(days=2)).isoformat()},
        ],
    })
    got = await cron.candidate_phones(client, NOW)
    assert got == {"5582recent"}


# ── _sync_one (reconciliação) ─────────────────────────────────────────────────

async def test_sync_one_writes_when_changed(monkeypatch):
    calls = {}

    async def fake_build(phone, now):
        return {"medico": "Dr. Júlio", "proxima_consulta": "x", "taxa_reserva": "Paga", "retornante": "Retornante"}

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nunca gravado

    async def fake_find_contact(phone):
        return 7

    async def fake_set_attrs(contact_id, attrs):
        calls["contact_id"] = contact_id
        calls["attrs"] = attrs

    async def fake_log_event(event_type, phone, metadata=None):
        calls["logged"] = (event_type, metadata)

    monkeypatch.setattr(cron, "_build_attrs_for_phone", fake_build)
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "find_or_create_contact_id", fake_find_contact)
    monkeypatch.setattr(cron, "set_contact_custom_attributes", fake_set_attrs)
    monkeypatch.setattr(cron, "log_event", fake_log_event)

    await cron._sync_one("5581111", NOW)

    assert calls["contact_id"] == 7
    assert calls["attrs"]["medico"] == "Dr. Júlio"
    assert calls["logged"][0] == "contact_attributes_synced"
    assert calls["logged"][1]["attrs"]["taxa_reserva"] == "Paga"


async def test_sync_one_skips_when_unchanged(monkeypatch):
    same = {"medico": "Dr. Júlio", "proxima_consulta": "x", "taxa_reserva": "Paga", "retornante": "Retornante"}

    async def fake_build(phone, now):
        return dict(same)

    async def fake_get_events(phone, event_type, limit=50):
        return [{"metadata": {"attrs": same}}]

    set_mock = AsyncMock()
    find_mock = AsyncMock()
    monkeypatch.setattr(cron, "_build_attrs_for_phone", fake_build)
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "set_contact_custom_attributes", set_mock)
    monkeypatch.setattr(cron, "find_or_create_contact_id", find_mock)

    await cron._sync_one("5581111", NOW)

    set_mock.assert_not_awaited()
    find_mock.assert_not_awaited()


async def test_sync_one_does_not_log_when_api_fails(monkeypatch):
    async def fake_build(phone, now):
        return {"medico": "Dr. Júlio", "proxima_consulta": "x", "taxa_reserva": "Paga", "retornante": "Retornante"}

    async def fake_get_events(phone, event_type, limit=50):
        return []

    async def fake_find_contact(phone):
        return 7

    async def fake_set_attrs(contact_id, attrs):
        raise RuntimeError("chatwoot down")

    log_mock = AsyncMock()
    monkeypatch.setattr(cron, "_build_attrs_for_phone", fake_build)
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "find_or_create_contact_id", fake_find_contact)
    monkeypatch.setattr(cron, "set_contact_custom_attributes", fake_set_attrs)
    monkeypatch.setattr(cron, "log_event", log_mock)

    await cron._sync_one("5581111", NOW)

    log_mock.assert_not_awaited()  # falha não grava a memória → próximo run tenta de novo


# ── _build_attrs_for_phone (fatos mockados) ───────────────────────────────────

async def test_build_attrs_for_phone_uses_next_appointment_patient(monkeypatch):
    async def fake_users(phone):
        return [
            {"id": "p1", "name": "Ana", "doctor_id": cron.DOCTOR_IDS_BY_KEY["julio"],
             "is_returning_patient": True, "custom_price": 200},
            {"id": "p2", "name": "João", "doctor_id": cron.DOCTOR_IDS_BY_KEY["bruna"],
             "is_returning_patient": False, "custom_price": 200},
        ]

    async def fake_upcoming(phone):
        return [{"status": "scheduled", "start_time": (NOW + timedelta(days=1)).isoformat(),
                 "modality": "online", "patient_id": "p2",
                 "booking_fee_paid_at": None, "booking_fee_waived": False}]

    async def fake_get_user(phone):
        return {"id": "p1"}

    monkeypatch.setattr(cron, "get_users_by_phone", fake_users)
    monkeypatch.setattr(cron, "get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)

    attrs = await cron._build_attrs_for_phone("5581111", NOW)
    assert attrs["medico"] == "Dra. Bruna"          # paciente da consulta mais próxima (p2)
    assert "João" in attrs["proxima_consulta"]
    assert attrs["retornante"] == "Primeira vez"
    assert attrs["taxa_reserva"] == "Pendente"


async def test_build_attrs_for_phone_no_upcoming_uses_recent_patient(monkeypatch):
    async def fake_users(phone):
        return [{"id": "p1", "name": "Ana", "doctor_id": cron.DOCTOR_IDS_BY_KEY["julio"],
                 "is_returning_patient": True, "custom_price": 200}]
    async def fake_upcoming(phone):
        return []  # nenhuma consulta futura
    async def fake_get_user(phone):
        return {"id": "p1", "name": "Ana", "doctor_id": cron.DOCTOR_IDS_BY_KEY["julio"],
                "is_returning_patient": True, "custom_price": 200}
    monkeypatch.setattr(cron, "get_users_by_phone", fake_users)
    monkeypatch.setattr(cron, "get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)

    attrs = await cron._build_attrs_for_phone("5581111", NOW)
    assert attrs["proxima_consulta"] == "sem consulta futura"
    assert attrs["taxa_reserva"] == ""
    assert attrs["medico"] == "Dr. Júlio"
    assert attrs["retornante"] == "Retornante"
