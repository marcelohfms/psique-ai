import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import attendant_routes
import attendant_db


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(attendant_routes.router)
    return TestClient(app)


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_resolve_requires_token(client):
    r = client.get("/api/atendente/resolve", params={"phone": "5581999998888"})
    assert r.status_code == 401


def test_resolve_wrong_token(client):
    r = client.get("/api/atendente/resolve",
                   params={"phone": "5581999998888", "token": "errado"})
    assert r.status_code == 401


# ── Leitura ───────────────────────────────────────────────────────────────────


def test_resolve_ok(client, monkeypatch):
    async def fake_resolve(phone):
        return {"contact": {"id": "c1", "name": "Maria"}, "patients": [{"id": "p1", "name": "João"}]}
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)
    r = client.get("/api/atendente/resolve",
                   params={"phone": "5581999998888", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["contact"]["id"] == "c1"
    assert body["patients"][0]["id"] == "p1"


def test_get_patient_ok(client, monkeypatch):
    async def fake_get_patient(pid):
        return {"id": "p1", "name": "João"}
    async def fake_get_link(pid, cid):
        return {"id": "pc1", "role": "agendamento"}
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "get_link", fake_get_link)
    r = client.get("/api/atendente/paciente/p1",
                   params={"contact_id": "c1", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["patient"]["id"] == "p1"
    assert body["link"]["id"] == "pc1"


# ── Escrita ───────────────────────────────────────────────────────────────────


def test_update_patient_calls_db_and_logs(client, monkeypatch):
    calls = {}
    async def fake_update(pid, data):
        calls["update"] = (pid, data)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "update_patient", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/paciente/p1",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888@s.whatsapp.net",
                          "data": {"name": "João Silva", "is_returning_patient": True}})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls["update"][0] == "p1"
    assert calls["update"][1]["name"] == "João Silva"
    assert calls["log"][0] == "attendant_edit_patient"


def test_update_patient_requires_token(client):
    r = client.post("/api/atendente/paciente/p1", json={"phone": "x", "data": {}})
    assert r.status_code == 401


def test_reset_checkpoint_endpoint(client, monkeypatch):
    calls = {}
    async def fake_reset(phone):
        calls["reset"] = phone
        return 3
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "reset_checkpoint", fake_reset)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/reset-checkpoint",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888@s.whatsapp.net"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": 3}
    assert calls["reset"] == "5581999998888@s.whatsapp.net"
    assert calls["log"][0] == "attendant_reset_checkpoint"


# ── App real: router montado + CSP ────────────────────────────────────────────


def test_main_app_includes_router_and_csp(monkeypatch):
    import main as dashboard_main

    async def fake_resolve(phone):
        return {"contact": None, "patients": []}
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)

    c = TestClient(dashboard_main.app)
    r = c.get("/api/atendente/resolve",
              params={"phone": "5581999998888", "token": "test-token"})
    assert r.status_code == 200
    assert "frame-ancestors" in r.headers.get("content-security-policy", "")
