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


def test_atendente_page_renders():
    import main as dashboard_main
    c = TestClient(dashboard_main.app)
    r = c.get("/atendente")
    assert r.status_code == 200
    assert "Painel da Eva" in r.text


import chatwoot_client
import payments


# ── Pagamentos ──────────────────────────────────────────────────────────────


def test_pagamentos_requires_token(client):
    r = client.get("/api/atendente/pagamentos", params={"phone": "5581999998888"})
    assert r.status_code == 401


def test_pagamentos_lista_filtrada_por_paciente(client, monkeypatch):
    async def fake_resolve(phone):
        return {"contact": {"id": "c1"}, "patients": [{"id": "p1"}, {"id": "p2"}]}
    async def fake_get_client():
        return object()
    async def fake_compute(_client, patient_ids=None):
        assert patient_ids == ["p1", "p2"]
        return [{"appointment_id": "a1", "tipo": "taxa", "valor": 100}]
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)
    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "compute_pendencias", fake_compute)

    r = client.get("/api/atendente/pagamentos",
                    params={"phone": "5581999998888", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["appointment_id"] == "a1"


def test_pagamentos_sem_contato_retorna_lista_vazia(client, monkeypatch):
    async def fake_resolve(phone):
        return {"contact": None, "patients": []}
    async def fake_get_client():
        return object()
    async def fake_compute(_client, patient_ids=None):
        assert patient_ids == []
        return []
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)
    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "compute_pendencias", fake_compute)

    r = client.get("/api/atendente/pagamentos",
                    params={"phone": "5581999998888", "token": "test-token"})
    assert r.status_code == 200
    assert r.json() == []


def test_pagar_requires_token(client):
    r = client.post("/api/atendente/pagamentos/a1/pagar", json={
        "tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
        "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
        "phone": "5581999998888",
    })
    assert r.status_code == 401


def test_pagar_tipo_invalido_retorna_400(client):
    r = client.post("/api/atendente/pagamentos/a1/pagar",
                    params={"token": "test-token"},
                    json={"tipo": "invalido", "valor": 100, "forma_pagamento": "PIX",
                          "paciente": "João", "medico": "Dr. Júlio",
                          "data_hora": "10/07/2026 14:00", "phone": "5581999998888"})
    assert r.status_code == 400


def test_pagar_registra_e_envia_confirmacao(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_paid(_client, appointment_id, tipo, valor, forma_pagamento,
                              paciente, medico, data_hora, phone, drive_link=""):
        calls["mark_paid"] = (appointment_id, tipo, valor)
    async def fake_send_confirmation(conversation_id, text):
        calls["confirm"] = (conversation_id, text)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    monkeypatch.setattr(chatwoot_client, "send_confirmation_message", fake_send_confirmation)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
              "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888", "conversation_id": 42},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls["mark_paid"] == ("a1", "taxa", 100)
    assert calls["confirm"][0] == 42
    assert "vaga está garantida" in calls["confirm"][1]
    assert calls["log"][0] == "attendant_pagamento_registrado"


def test_pagar_sem_conversation_id_nao_envia_confirmacao(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_paid(*args, **kwargs):
        calls["mark_paid"] = True
    async def fake_send_confirmation(conversation_id, text):
        calls["confirm"] = True
    async def fake_log(event_type, phone, metadata):
        calls["log"] = True

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    monkeypatch.setattr(chatwoot_client, "send_confirmation_message", fake_send_confirmation)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": "consulta", "valor": 700, "forma_pagamento": "PIX",
              "paciente": "Maria", "medico": "Dra. Bruna", "data_hora": "10/07/2026 15:00",
              "phone": "5581999998888"},
    )
    assert r.status_code == 200
    assert calls["mark_paid"] is True
    assert "confirm" not in calls  # sem conversation_id, não tenta mandar mensagem


def test_pagar_falha_no_envio_da_confirmacao_nao_quebra(client, monkeypatch):
    async def fake_get_client():
        return object()
    async def fake_mark_paid(*args, **kwargs):
        return None
    async def fake_send_confirmation(conversation_id, text):
        raise RuntimeError("chatwoot fora do ar")
    async def fake_log(event_type, phone, metadata):
        return None

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    monkeypatch.setattr(chatwoot_client, "send_confirmation_message", fake_send_confirmation)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
              "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888", "conversation_id": 42},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── Isenção de taxa de reserva ────────────────────────────────────────────────


def test_isentar_requires_token(client):
    r = client.post("/api/atendente/pagamentos/a1/isentar", json={
        "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
        "phone": "5581999998888",
    })
    assert r.status_code == 401


def test_isentar_registra_e_envia_confirmacao(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_fee_waived(_client, appointment_id, paciente, medico, data_hora):
        calls["mark_fee_waived"] = (appointment_id, paciente, medico, data_hora)
    async def fake_send_confirmation(conversation_id, text):
        calls["confirm"] = (conversation_id, text)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_fee_waived", fake_mark_fee_waived)
    monkeypatch.setattr(chatwoot_client, "send_confirmation_message", fake_send_confirmation)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/isentar",
        params={"token": "test-token"},
        json={"paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888", "conversation_id": 42},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls["mark_fee_waived"] == ("a1", "João", "Dr. Júlio", "10/07/2026 14:00")
    assert calls["confirm"][0] == 42
    assert "isentada" in calls["confirm"][1]
    assert calls["log"][0] == "attendant_taxa_isentada"


def test_isentar_sem_conversation_id_nao_envia_confirmacao(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_fee_waived(*args, **kwargs):
        calls["mark_fee_waived"] = True
    async def fake_send_confirmation(conversation_id, text):
        calls["confirm"] = True
    async def fake_log(event_type, phone, metadata):
        calls["log"] = True

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_fee_waived", fake_mark_fee_waived)
    monkeypatch.setattr(chatwoot_client, "send_confirmation_message", fake_send_confirmation)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/isentar",
        params={"token": "test-token"},
        json={"paciente": "Maria", "medico": "Dra. Bruna", "data_hora": "10/07/2026 15:00",
              "phone": "5581999998888"},
    )
    assert r.status_code == 200
    assert calls["mark_fee_waived"] is True
    assert "confirm" not in calls  # sem conversation_id, não tenta mandar mensagem


def test_isentar_falha_no_envio_da_confirmacao_nao_quebra(client, monkeypatch):
    async def fake_get_client():
        return object()
    async def fake_mark_fee_waived(*args, **kwargs):
        return None
    async def fake_send_confirmation(conversation_id, text):
        raise RuntimeError("chatwoot fora do ar")
    async def fake_log(event_type, phone, metadata):
        return None

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_fee_waived", fake_mark_fee_waived)
    monkeypatch.setattr(chatwoot_client, "send_confirmation_message", fake_send_confirmation)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/isentar",
        params={"token": "test-token"},
        json={"paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888", "conversation_id": 42},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── Upload de comprovante ─────────────────────────────────────────────────────


def test_upload_comprovante_requires_token(client):
    r = client.post(
        "/api/atendente/pagamentos/a1/comprovante",
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 401


def test_upload_comprovante_retorna_drive_link(client, monkeypatch):
    calls = {}
    async def fake_upload(patient_name, appointment_dt, amount, file_bytes, mimetype):
        calls["upload"] = (patient_name, appointment_dt, amount, file_bytes, mimetype)
        return "https://drive.google.com/file/d/abc123/view"
    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)

    r = client.post(
        "/api/atendente/pagamentos/a1/comprovante",
        params={"token": "test-token"},
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {"drive_link": "https://drive.google.com/file/d/abc123/view"}
    assert calls["upload"] == ("João", "10/07/2026 14:00", "100", b"fake-image-bytes", "image/jpeg")


def test_upload_comprovante_falha_no_drive_retorna_502(client, monkeypatch):
    async def fake_upload(*args, **kwargs):
        raise RuntimeError("Drive indisponível")
    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)

    r = client.post(
        "/api/atendente/pagamentos/a1/comprovante",
        params={"token": "test-token"},
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 502


def test_pagar_repassa_drive_link_para_mark_paid(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_paid(_client, appointment_id, tipo, valor, forma_pagamento,
                              paciente, medico, data_hora, phone, drive_link=""):
        calls["drive_link"] = drive_link
    async def fake_log(event_type, phone, metadata):
        return None

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)

    r = client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": "consulta", "valor": 550, "forma_pagamento": "PIX",
              "paciente": "Natalia", "medico": "Dra. Bruna", "data_hora": "01/07/2026 15:00",
              "phone": "5581999688071",
              "drive_link": "https://drive.google.com/file/d/abc123/view"},
    )
    assert r.status_code == 200
    assert calls["drive_link"] == "https://drive.google.com/file/d/abc123/view"
