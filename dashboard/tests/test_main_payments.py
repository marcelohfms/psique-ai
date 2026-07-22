from starlette.testclient import TestClient

import main as dashboard_main
import payments

AUTH = ("user", "changeme")


def _client():
    return TestClient(dashboard_main.app)


def test_api_pagar_requires_auth():
    r = _client().post(
        "/api/pagamentos/a1/pagar",
        json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
              "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888"},
    )
    assert r.status_code == 401


def test_api_pagar_repassa_drive_link_para_mark_paid(monkeypatch):
    calls = {}

    async def fake_mark_paid(_client, appointment_id, tipo, valor, forma_pagamento,
                              paciente, medico, data_hora, phone, drive_link=""):
        calls["drive_link"] = drive_link

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)

    r = _client().post(
        "/api/pagamentos/a1/pagar",
        auth=AUTH,
        json={"tipo": "consulta", "valor": 550, "forma_pagamento": "PIX",
              "paciente": "Natalia", "medico": "Dra. Bruna", "data_hora": "01/07/2026 15:00",
              "phone": "5581999688071",
              "drive_link": "https://drive.google.com/file/d/abc123/view"},
    )
    assert r.status_code == 200
    assert calls["drive_link"] == "https://drive.google.com/file/d/abc123/view"


def test_api_upload_comprovante_requires_auth():
    r = _client().post(
        "/api/pagamentos/a1/comprovante",
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 401


def test_api_upload_comprovante_retorna_drive_link(monkeypatch):
    calls = {}

    async def fake_upload(patient_name, appointment_dt, amount, file_bytes, mimetype):
        calls["upload"] = (patient_name, appointment_dt, amount, file_bytes, mimetype)
        return "https://drive.google.com/file/d/abc123/view"

    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)

    r = _client().post(
        "/api/pagamentos/a1/comprovante",
        auth=AUTH,
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {"drive_link": "https://drive.google.com/file/d/abc123/view"}
    assert calls["upload"] == ("João", "10/07/2026 14:00", "100", b"fake-image-bytes", "image/jpeg")


def test_api_upload_comprovante_falha_no_drive_retorna_502(monkeypatch):
    async def fake_upload(*args, **kwargs):
        raise RuntimeError("Drive indisponível")

    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)

    r = _client().post(
        "/api/pagamentos/a1/comprovante",
        auth=AUTH,
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 502
