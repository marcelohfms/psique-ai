from starlette.testclient import TestClient

import main as dashboard_main
import payments
import return_reminders

AUTH = ("user", "s3nha-teste")  # DASHBOARD_PASSWORD do conftest


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
                              paciente, medico, data_hora, phone, drive_link="",
                              receipt_filename=""):
        calls["drive_link"] = drive_link
        calls["receipt_filename"] = receipt_filename

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)

    r = _client().post(
        "/api/pagamentos/a1/pagar",
        auth=AUTH,
        json={"tipo": "consulta", "valor": 550, "forma_pagamento": "PIX",
              "paciente": "Natalia", "medico": "Dra. Bruna", "data_hora": "01/07/2026 15:00",
              "phone": "5581999688071",
              "drive_link": "https://drive.google.com/file/d/abc123/view",
              "receipt_filename": "Natalia_01-07-2026_R$550.pdf"},
    )
    assert r.status_code == 200
    assert calls["drive_link"] == "https://drive.google.com/file/d/abc123/view"
    # O nome real do arquivo no Drive volta do upload e tem que chegar inteiro à
    # planilha — é ele que a atendente usa para achar o comprovante.
    assert calls["receipt_filename"] == "Natalia_01-07-2026_R$550.pdf"


def test_api_pagamentos_no_show_requires_auth():
    r = _client().post("/api/pagamentos/a1/no-show")
    assert r.status_code == 401


def test_api_pagamentos_no_show_marca_falta(monkeypatch):
    calls = {}

    async def fake_mark_no_show(_client, appointment_id):
        calls["appointment_id"] = appointment_id

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(return_reminders, "mark_no_show", fake_mark_no_show)

    r = _client().post("/api/pagamentos/a1/no-show", auth=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls["appointment_id"] == "a1"


def test_api_upload_comprovante_requires_auth():
    r = _client().post(
        "/api/pagamentos/a1/comprovante",
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 401


def test_api_upload_comprovante_retorna_drive_link_e_nome(monkeypatch):
    calls = {}

    async def fake_upload(patient_name, appointment_dt, amount, file_bytes, mimetype):
        calls["upload"] = (patient_name, appointment_dt, amount, file_bytes, mimetype)
        return "https://drive.google.com/file/d/abc123/view", "João_10-07-2026_R$100.jpg"

    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)

    r = _client().post(
        "/api/pagamentos/a1/comprovante",
        auth=AUTH,
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {
        "drive_link": "https://drive.google.com/file/d/abc123/view",
        "receipt_filename": "João_10-07-2026_R$100.jpg",
    }
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
