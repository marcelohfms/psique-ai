"""Escopo por contato/paciente nas rotas de escrita do painel (achado IDOR).

O token do painel é único e não amarra o chamador a um paciente. Antes, quem o
tivesse podia trocar o ID no path/body e agir sobre QUALQUER paciente/consulta.
Agora cada rota de escrita confere que o objeto alvo pertence ao telefone da
requisição (o mesmo que a conversa aberta no Chatwoot), devolvendo 403 quando não.
"""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import attendant_db
import attendant_routes
import payments
import return_reminders

TOKEN = {"token": "test-token"}
PHONE = "5581999998888"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(attendant_routes.router)
    return TestClient(app)


def _scope(monkeypatch, contact_id, patient_ids):
    async def fake_scope(phone):
        return contact_id, set(patient_ids)
    monkeypatch.setattr(attendant_db, "scope_for_phone", fake_scope)


# ── paciente ────────────────────────────────────────────────────────────────

def test_update_paciente_fora_do_escopo_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    called = {"n": 0}
    async def fake_update(pid, data):
        called["n"] += 1
    monkeypatch.setattr(attendant_db, "update_patient", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", _noop)
    r = client.post("/api/atendente/paciente/p_ALHEIO", params=TOKEN,
                    json={"phone": PHONE, "data": {"name": "X"}})
    assert r.status_code == 403
    assert called["n"] == 0  # não tocou no banco


def test_update_paciente_no_escopo_ok(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    async def fake_update(pid, data):
        assert pid == "p1"
    monkeypatch.setattr(attendant_db, "update_patient", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", _noop)
    r = client.post("/api/atendente/paciente/p1", params=TOKEN,
                    json={"phone": PHONE, "data": {"name": "X"}})
    assert r.status_code == 200


# ── contato ─────────────────────────────────────────────────────────────────

def test_update_contato_fora_do_escopo_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    monkeypatch.setattr(attendant_db, "update_contact", _noop2)
    monkeypatch.setattr(attendant_db, "log_event", _noop)
    r = client.post("/api/atendente/contato/c_ALHEIO", params=TOKEN,
                    json={"phone": PHONE, "data": {"name": "X"}})
    assert r.status_code == 403


# ── vínculo ─────────────────────────────────────────────────────────────────

def test_update_vinculo_fora_do_escopo_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    async def fake_link(pc_id):
        return {"id": pc_id, "patient_id": "p_ALHEIO", "contact_id": "c_ALHEIO"}
    monkeypatch.setattr(attendant_db, "get_link_by_id", fake_link)
    monkeypatch.setattr(attendant_db, "update_link", _noop2)
    monkeypatch.setattr(attendant_db, "log_event", _noop)
    r = client.post("/api/atendente/vinculo/pc_ALHEIO", params=TOKEN,
                    json={"phone": PHONE, "data": {"role": "agendamento"}})
    assert r.status_code == 403


# ── pagamento ────────────────────────────────────────────────────────────────

def test_pagar_fora_do_escopo_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    async def fake_appt_pid(appt):
        return "p_ALHEIO"
    monkeypatch.setattr(attendant_db, "get_appointment_patient_id", fake_appt_pid)
    monkeypatch.setattr(attendant_db, "log_event", _noop)
    r = client.post("/api/atendente/pagamentos/a_ALHEIO/pagar", params=TOKEN,
                    json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
                          "paciente": "X", "medico": "Dr", "data_hora": "01/01/2026 10:00",
                          "phone": PHONE})
    assert r.status_code == 403


def test_no_show_fora_do_escopo_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    async def fake_appt_pid(appt):
        return "p_ALHEIO"
    monkeypatch.setattr(attendant_db, "get_appointment_patient_id", fake_appt_pid)
    r = client.post("/api/atendente/pagamentos/a_ALHEIO/no-show",
                    params={**TOKEN, "phone": PHONE})
    assert r.status_code == 403


async def _noop(*a, **k):
    return None


async def _noop2(*a, **k):
    return None


# ── Regressão: coluna certa nas queries de escopo ────────────────────────────
# get_appointment_patient_id consultava a coluna errada (`id`, um UUID) usando o
# appointment_id, que é um id de evento do Google Calendar (texto). Em produção
# isso estourava com erro de UUID no Postgres e derrubava pagar/isentar/no-show
# do painel. Os testes de rota mockam essa função, então não pegavam. Estes
# exercitam a query real contra o FakeClient (que casa pelo NOME da coluna).

async def test_get_appointment_patient_id_usa_coluna_appointment_id(monkeypatch, fake_client):
    fake_client.store["appointments"] = [
        {"id": "uuid-interno", "appointment_id": "cal-event-123", "patient_id": "p-123"},
    ]
    async def fake_get_client():
        return fake_client
    monkeypatch.setattr(attendant_db, "get_client", fake_get_client)
    pid = await attendant_db.get_appointment_patient_id("cal-event-123")
    assert pid == "p-123"


async def test_get_appointment_patient_id_inexistente_retorna_none(monkeypatch, fake_client):
    fake_client.store["appointments"] = [
        {"id": "uuid-interno", "appointment_id": "cal-event-123", "patient_id": "p-123"},
    ]
    async def fake_get_client():
        return fake_client
    monkeypatch.setattr(attendant_db, "get_client", fake_get_client)
    assert await attendant_db.get_appointment_patient_id("nao-existe") is None


async def test_get_link_by_id_usa_coluna_id(monkeypatch, fake_client):
    fake_client.store["patient_contacts"] = [
        {"id": "pc-1", "patient_id": "p-1", "contact_id": "c-1"},
    ]
    async def fake_get_client():
        return fake_client
    monkeypatch.setattr(attendant_db, "get_client", fake_get_client)
    link = await attendant_db.get_link_by_id("pc-1")
    assert link and link["patient_id"] == "p-1" and link["contact_id"] == "c-1"
