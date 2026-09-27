import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import attendant_routes
import attendant_db


@pytest.fixture(autouse=True)
def _bypass_scope(monkeypatch):
    """Neutraliza os guards de escopo por contato/paciente nestes testes.

    A aplicação do escopo (403 fora do contato) é coberta em test_attendant_scope.py.
    Aqui o foco é o comportamento de mutação/confirmação de cada rota, então os
    guards são no-op para não precisarem de um telefone que resolva no banco fake."""
    async def _ok(*a, **k):
        return None
    for name in ("_assert_contact_scope", "_assert_patient_scope",
                 "_assert_link_scope", "_assert_appointment_scope"):
        monkeypatch.setattr(attendant_routes, name, _ok)


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
    async def fake_get_rr(pid):
        return None
    async def fake_eva_off(pid):
        return False
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "get_link", fake_get_link)
    monkeypatch.setattr(attendant_db, "get_return_reminder", fake_get_rr)
    monkeypatch.setattr(attendant_db, "is_patient_eva_off", fake_eva_off)
    r = client.get("/api/atendente/paciente/p1",
                   params={"contact_id": "c1", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["patient"]["id"] == "p1"
    assert body["link"]["id"] == "pc1"


def test_get_patient_includes_return_reminder(client, monkeypatch):
    async def fake_get_patient(pid):
        return {"id": "p1", "name": "João"}
    async def fake_get_link(pid, cid):
        return {"id": "pc1"}
    async def fake_get_rr(pid):
        return {"next_return_date": "2026-09-15", "return_interval": "2_meses", "doctor_id": "d1"}
    async def fake_eva_off(pid):
        return False
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "get_link", fake_get_link)
    monkeypatch.setattr(attendant_db, "get_return_reminder", fake_get_rr)
    monkeypatch.setattr(attendant_db, "is_patient_eva_off", fake_eva_off)
    r = client.get("/api/atendente/paciente/p1",
                   params={"contact_id": "c1", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["return_reminder"]["next_return_date"] == "2026-09-15"
    assert body["return_reminder"]["return_interval"] == "2_meses"


# ── Escrita ───────────────────────────────────────────────────────────────────


def test_update_contato_calls_db_e_loga_agent(client, monkeypatch):
    calls = {}
    async def fake_update(cid, data):
        calls["update"] = (cid, data)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "update_contact", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/contato/c1",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888", "data": {"name": "Carla"}, "agent": "Ana"})
    assert r.status_code == 200
    assert calls["update"] == ("c1", {"name": "Carla"})
    assert calls["log"][0] == "attendant_edit_contact"
    assert calls["log"][2]["agent"] == "Ana"


def test_update_contato_recusa_nome_vazio_400(client, monkeypatch):
    async def fake_update(cid, data):
        raise AssertionError("não deve chamar o db com nome vazio")
    monkeypatch.setattr(attendant_db, "update_contact", fake_update)
    r = client.post("/api/atendente/contato/c1",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888", "data": {"name": "   "}})
    assert r.status_code == 400


def test_update_contato_sem_campo_name_nao_exige_nome(client, monkeypatch):
    """O 400 só se aplica quando `name` está no payload; editar só o CPF, por
    exemplo, não deve exigir o nome junto."""
    calls = {}
    async def fake_update(cid, data):
        calls["update"] = (cid, data)
    async def fake_log(*a, **k):
        return None
    monkeypatch.setattr(attendant_db, "update_contact", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/contato/c1",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888", "data": {"cpf": "12345678900"}})
    assert r.status_code == 200
    assert calls["update"] == ("c1", {"cpf": "12345678900"})


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


def test_update_return_date_ok(client, monkeypatch):
    calls = {}
    async def fake_update(pid, data):
        calls["update"] = (pid, data)
        return True
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "update_return_reminder", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/paciente/p1/retorno",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888", "data": {"next_return_date": "2026-10-15"}})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "updated": True}
    assert calls["update"] == ("p1", {"next_return_date": "2026-10-15"})
    assert calls["log"][0] == "attendant_edit_return_date"


def test_update_return_date_invalid_date_400(client, monkeypatch):
    async def fake_update(pid, data):
        raise AssertionError("não deve chamar o db com data inválida")
    monkeypatch.setattr(attendant_db, "update_return_reminder", fake_update)
    r = client.post("/api/atendente/paciente/p1/retorno",
                    params={"token": "test-token"},
                    json={"phone": "x", "data": {"next_return_date": "15/10/2026"}})
    assert r.status_code == 400


def test_update_return_date_missing_field_400(client):
    r = client.post("/api/atendente/paciente/p1/retorno",
                    params={"token": "test-token"},
                    json={"phone": "x", "data": {}})
    assert r.status_code == 400


def test_update_return_date_requires_token(client):
    r = client.post("/api/atendente/paciente/p1/retorno",
                    json={"phone": "x", "data": {"next_return_date": "2026-10-15"}})
    assert r.status_code == 401


def test_desligar_eva_liga_manual_hold(client, monkeypatch):
    chamado = {}
    async def _set(pid, off):
        chamado["args"] = (pid, off); return 2
    async def _log(*a, **k):
        return None
    monkeypatch.setattr(attendant_db, "set_patient_eva_off", _set)
    monkeypatch.setattr(attendant_db, "log_event", _log)

    r = client.post("/api/atendente/paciente/p1/eva",
                    params={"token": "test-token"},
                    json={"phone": "5581999", "data": {"off": True}})
    assert r.status_code == 200
    assert r.json()["afetados"] == 2
    assert chamado["args"] == ("p1", True)


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
    # O Chatwoot abre o iframe com ?token=...; sem o token a página é recusada
    # (senão qualquer visitante anônimo receberia o segredo do painel).
    r = c.get("/atendente", params={"token": "test-token"})
    assert r.status_code == 200
    # A moldura nova (Task 11) tirou o título "Painel da Eva": o nome do
    # contato no topo (#contact-name) passou a cumprir esse papel.
    assert 'id="contact-name"' in r.text


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
                              paciente, medico, data_hora, phone, drive_link="",
                              receipt_filename=""):
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


def test_upload_comprovante_retorna_drive_link_e_nome(client, monkeypatch):
    calls = {}
    async def fake_upload(patient_name, appointment_dt, amount, file_bytes, mimetype):
        calls["upload"] = (patient_name, appointment_dt, amount, file_bytes, mimetype)
        return "https://drive.google.com/file/d/abc123/view", "João_10-07-2026_R$100.jpg"
    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)

    r = client.post(
        "/api/atendente/pagamentos/a1/comprovante",
        params={"token": "test-token"},
        data={"paciente": "João", "data_hora": "10/07/2026 14:00", "valor": "100"},
        files={"file": ("comprovante.jpg", b"fake-image-bytes", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {
        "drive_link": "https://drive.google.com/file/d/abc123/view",
        "receipt_filename": "João_10-07-2026_R$100.jpg",
    }
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
                              paciente, medico, data_hora, phone, drive_link="",
                              receipt_filename=""):
        calls["drive_link"] = drive_link
        calls["receipt_filename"] = receipt_filename
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
              "drive_link": "https://drive.google.com/file/d/abc123/view",
              "receipt_filename": "Natalia_01-07-2026_R$550.pdf"},
    )
    assert r.status_code == 200
    assert calls["drive_link"] == "https://drive.google.com/file/d/abc123/view"
    assert calls["receipt_filename"] == "Natalia_01-07-2026_R$550.pdf"


# ── No-show (falta) ─────────────────────────────────────────────────────────


import return_reminders


def test_pagamentos_no_show_requires_token(client):
    r = client.post("/api/atendente/pagamentos/a1/no-show")
    assert r.status_code == 401


def test_pagamentos_no_show_marca_falta(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_no_show(_client, appointment_id):
        calls["appointment_id"] = appointment_id

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(return_reminders, "mark_no_show", fake_mark_no_show)

    r = client.post("/api/atendente/pagamentos/a1/no-show",
                    params={"token": "test-token", "phone": "5581999998888"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls["appointment_id"] == "a1"


# ── Vínculo: busca, vincular, ficha nova, desvincular ────────────────────────

T = {"token": "test-token"}


def _scope_c1(monkeypatch, patient_ids=("p1",)):
    async def fake_scope(phone):
        return "c1", set(patient_ids)
    monkeypatch.setattr(attendant_db, "scope_for_phone", fake_scope)


def _events(monkeypatch):
    got = []
    async def fake_log(event_type, phone, metadata=None):
        got.append((event_type, metadata))
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    return got


def test_busca_devolve_resultados(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_search(q):
        assert q == "joao"
        return [{"id": "p9", "name": "João", "birth_date": None, "phone_hint": None}]
    monkeypatch.setattr(attendant_db, "search_patients", fake_search)
    r = client.get("/api/atendente/pacientes/busca", params={**T, "q": "joao", "phone": "5581"})
    assert r.status_code == 200 and r.json()[0]["id"] == "p9"


def test_busca_recusa_q_maior_que_80_caracteres(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.get("/api/atendente/pacientes/busca",
                   params={**T, "q": "a" * 81, "phone": "5581"})
    assert r.status_code == 422


def test_busca_recusa_agent_maior_que_80_caracteres(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.get("/api/atendente/pacientes/busca",
                   params={**T, "q": "joao", "phone": "5581", "agent": "a" * 81})
    assert r.status_code == 422


def test_busca_audita_com_termo_e_quantidade_de_resultados(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    async def fake_search(q):
        return [{"id": "p9"}, {"id": "p10"}]
    monkeypatch.setattr(attendant_db, "search_patients", fake_search)
    r = client.get("/api/atendente/pacientes/busca",
                   params={**T, "q": "joao", "phone": "5581", "agent": "Ana"})
    assert r.status_code == 200
    assert ev[0] == ("attendant_search", {"q": "joao", "agent": "Ana", "results": 2})


def test_vincular_usa_o_contato_do_telefone_e_normaliza(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    calls = []
    async def fake_get_patient(pid):
        return {"id": pid}
    async def fake_link(pid, cid, marker):
        calls.append((pid, cid, marker))
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "link_patient", fake_link)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "p9", "is_self": True, "relationship": "mãe", "agent": "Ana"})
    assert r.status_code == 200
    assert calls == [("p9", "c1", {"is_self": True, "relationship": None})]
    assert ev[0][0] == "attendant_link_patient" and ev[0][1]["agent"] == "Ana"


def test_vincular_recusa_agent_maior_que_80_caracteres(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "p9", "is_self": True, "agent": "a" * 81})
    assert r.status_code == 422


def test_vincular_recusa_parentesco_fora_da_lista(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "p9", "is_self": False, "relationship": "vizinha"})
    assert r.status_code == 400


def test_vincular_paciente_inexistente_404(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_get_patient(pid):
        return None
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "nope", "is_self": True})
    assert r.status_code == 404


def test_ficha_nova_duplicada_devolve_409_com_a_ficha(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_find(name, birth):
        return [{"id": "p1", "name": "João Menezes", "birth_date": "06/05/2014"}]
    monkeypatch.setattr(attendant_db, "find_patients_by_name_birth", fake_find)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "joao menezes", "birth_date": "2014-05-06"})
    assert r.status_code == 409
    assert r.json()["detail"]["duplicates"][0]["id"] == "p1"


def test_ficha_nova_cria(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    async def fake_find(name, birth):
        return []
    async def fake_create(name, birth):
        assert (name, birth) == ("Ana Luz", "01/02/2015")
        return {"id": "novo", "name": name, "birth_date": birth}
    monkeypatch.setattr(attendant_db, "find_patients_by_name_birth", fake_find)
    monkeypatch.setattr(attendant_db, "create_patient", fake_create)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": " Ana  Luz ", "birth_date": "01/02/2015"})
    assert r.status_code == 200 and r.json()["patient"]["id"] == "novo"
    assert ev[0][0] == "attendant_create_patient"


def test_ficha_nova_create_patient_value_error_vira_400(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_find(name, birth):
        return []
    async def fake_create(name, birth):
        raise ValueError("Nome vazio.")
    monkeypatch.setattr(attendant_db, "find_patients_by_name_birth", fake_find)
    monkeypatch.setattr(attendant_db, "create_patient", fake_create)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "Ana Luz", "birth_date": "01/02/2015"})
    assert r.status_code == 400


def test_ficha_nova_recusa_agent_maior_que_80_caracteres(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "Ana Luz", "birth_date": "01/02/2015", "agent": "a" * 81})
    assert r.status_code == 422


def test_ficha_nova_nascimento_invalido_400(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "Ana Luz", "birth_date": "31/02/2015"})
    assert r.status_code == 400


def test_ficha_nova_nome_com_2_caracteres_400(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "Jo", "birth_date": "01/02/2015"})
    assert r.status_code == 400


def test_desvincular_travado_409(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_blocker(pid, cid):
        return "Este é o único número do paciente..."
    monkeypatch.setattr(attendant_db, "unlink_blocker", fake_blocker)
    r = client.post("/api/atendente/desvincular", params=T, json={"phone": "5581", "patient_id": "p1"})
    assert r.status_code == 409 and "único número" in r.json()["detail"]["message"]


def test_desvincular_recusa_agent_maior_que_80_caracteres(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/desvincular", params=T, json={
        "phone": "5581", "patient_id": "p1", "agent": "a" * 81})
    assert r.status_code == 422


def test_desvincular_ok(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    async def fake_blocker(pid, cid):
        return None
    async def fake_future_bookings(pid, cid):
        return 0
    async def fake_unlink(pid, cid):
        assert (pid, cid) == ("p1", "c1")
        return 3
    monkeypatch.setattr(attendant_db, "unlink_blocker", fake_blocker)
    monkeypatch.setattr(attendant_db, "future_bookings_by_contact", fake_future_bookings)
    monkeypatch.setattr(attendant_db, "unlink_patient", fake_unlink)
    r = client.post("/api/atendente/desvincular", params=T, json={"phone": "5581", "patient_id": "p1"})
    body = r.json()
    assert r.status_code == 200 and body["removed"] == 3
    assert body["still_booking"] == 0
    assert ev[0][0] == "attendant_unlink_patient"
    assert ev[0][1]["still_booking"] == 0


def test_desvincular_avisa_consultas_futuras_ainda_no_numero(client, monkeypatch):
    """future_bookings_by_contact é chamado ANTES de apagar o vínculo (o dado
    precisa ser lido antes que unlink_patient rode) e o resultado volta na
    resposta e no log."""
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    order = []
    async def fake_blocker(pid, cid):
        return None
    async def fake_future_bookings(pid, cid):
        order.append("future_bookings")
        return 2
    async def fake_unlink(pid, cid):
        order.append("unlink")
        return 3
    monkeypatch.setattr(attendant_db, "unlink_blocker", fake_blocker)
    monkeypatch.setattr(attendant_db, "future_bookings_by_contact", fake_future_bookings)
    monkeypatch.setattr(attendant_db, "unlink_patient", fake_unlink)
    r = client.post("/api/atendente/desvincular", params=T, json={"phone": "5581", "patient_id": "p1"})
    assert r.status_code == 200
    assert r.json()["still_booking"] == 2
    assert order == ["future_bookings", "unlink"]
    assert ev[0][1]["still_booking"] == 2


def test_editar_vinculo_normaliza_marcador(client, monkeypatch):
    got = {}
    async def fake_update(pc_id, data):
        got.update(data)
    async def fake_log(*a, **k):
        return None
    monkeypatch.setattr(attendant_db, "update_link", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/vinculo/pc1", params=T,
                    json={"phone": "5581", "data": {"is_self": True, "relationship": "mãe"}})
    assert r.status_code == 200 and got == {"is_self": True, "relationship": None}
    r = client.post("/api/atendente/vinculo/pc1", params=T,
                    json={"phone": "5581", "data": {"is_self": False, "relationship": "vizinha"}})
    assert r.status_code == 400


def test_editar_vinculo_loga_agent(client, monkeypatch):
    calls = {}
    async def fake_update(pc_id, data):
        return None
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "update_link", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/vinculo/pc1", params=T,
                    json={"phone": "5581", "data": {"is_self": True, "relationship": "mãe"},
                          "agent": "Ana"})
    assert r.status_code == 200
    assert calls["log"][0] == "attendant_edit_link"
    assert calls["log"][2]["agent"] == "Ana"
