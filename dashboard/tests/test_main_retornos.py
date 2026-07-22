from starlette.testclient import TestClient

import main as dashboard_main
import return_reminders

AUTH = ("user", "changeme")
JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"


def _client():
    return TestClient(dashboard_main.app)


def test_retornos_page_requires_auth():
    r = _client().get("/retornos")
    assert r.status_code == 401


def test_retornos_page_ok(monkeypatch):
    async def fake_today(client, doctor_id, today=None):
        return [{"appointment_id": "a1", "patient_id": "p1", "start_time": "2026-07-13T12:00:00+00:00",
                 "patients": {"name": "João"}}]

    async def fake_pending(client, doctor_id):
        return []

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(return_reminders, "get_today_appointments", fake_today)
    monkeypatch.setattr(return_reminders, "get_pending_classification", fake_pending)

    r = _client().get("/retornos", auth=AUTH, params={"medico": "julio"})
    assert r.status_code == 200
    assert "João" in r.text


def test_api_salvar_retorno_requires_auth():
    r = _client().post(
        "/api/retornos/p1",
        json={"doctor_id": JULIO_ID, "appointment_id": "a1",
              "appointment_date": "2026-07-13", "return_interval": "3_meses"},
    )
    assert r.status_code == 401


def test_api_salvar_retorno_ok(monkeypatch):
    calls = {}

    async def fake_save(client, patient_id, doctor_id, appointment_id, appointment_date, return_interval):
        calls["args"] = (patient_id, doctor_id, appointment_id, appointment_date, return_interval)
        return {"patient_id": patient_id, "return_interval": return_interval}

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(return_reminders, "save_classification", fake_save)

    r = _client().post(
        "/api/retornos/p1",
        auth=AUTH,
        json={"doctor_id": JULIO_ID, "appointment_id": "a1",
              "appointment_date": "2026-07-13", "return_interval": "3_meses"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    from datetime import date
    assert calls["args"] == ("p1", JULIO_ID, "a1", date(2026, 7, 13), "3_meses")


def test_api_salvar_retorno_interval_invalido_retorna_400():
    r = _client().post(
        "/api/retornos/p1",
        auth=AUTH,
        json={"doctor_id": JULIO_ID, "appointment_id": "a1",
              "appointment_date": "2026-07-13", "return_interval": "2_meses"},
    )
    assert r.status_code == 400
