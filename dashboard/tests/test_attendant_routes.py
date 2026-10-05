import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import attendant_routes
import eva_client
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


@pytest.fixture(autouse=True)
def _confirm_defaults(monkeypatch):
    """Padrão da confirmação de pagamento: contato sem ficha no banco (trata como
    o próprio paciente). Testes que precisam de outro cenário sobrescrevem com
    monkeypatch."""
    async def _no_contact(phone):
        return {"contact": None, "patients": []}
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", _no_contact)


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


def _eva_recorder(monkeypatch, status=200):
    """A confirmação de pagamento sai pela Eva (/admin/panel/payment-confirmation)."""
    sent = []
    async def fake_post(path, body, timeout=30.0):
        sent.append((path, body))
        return status, {"sent": status == 200}
    monkeypatch.setattr(eva_client, "post", fake_post)
    return sent


def test_pagar_registra_e_envia_confirmacao(client, monkeypatch):
    calls = {}
    async def fake_get_client():
        return object()
    async def fake_mark_paid(_client, appointment_id, tipo, valor, forma_pagamento,
                              paciente, medico, data_hora, phone, drive_link="",
                              receipt_filename=""):
        calls["mark_paid"] = (appointment_id, tipo, valor)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    sent = _eva_recorder(monkeypatch)

    r = client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
              "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888", "conversation_id": 42},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "confirmacao": "enviada"}
    assert calls["mark_paid"] == ("a1", "taxa", 100)
    path, body = sent[0]
    assert path == "/admin/panel/payment-confirmation"
    assert body["phone"] == "5581999998888"
    assert body["template"] == "pagamento_taxa_recebido"
    assert "R$ 100,00" in body["text"]
    assert "sua consulta está garantida" in body["text"]
    assert calls["log"][0] == "attendant_pagamento_registrado"


def test_pagar_sem_conversation_id_envia_confirmacao_mesmo_assim(client, monkeypatch):
    """A Eva acha a conversa pelo telefone; o conversation_id do iframe não é mais
    condição para avisar o paciente."""
    async def fake_get_client():
        return object()
    async def fake_noop(*args, **kwargs):
        return None
    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_noop)
    monkeypatch.setattr(attendant_db, "log_event", fake_noop)
    sent = _eva_recorder(monkeypatch)

    r = client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": "consulta", "valor": 700, "forma_pagamento": "PIX",
              "paciente": "Maria", "medico": "Dra. Bruna", "data_hora": "10/07/2026 15:00",
              "phone": "5581999998888"},
    )
    assert r.status_code == 200
    assert len(sent) == 1


def test_pagar_falha_no_envio_da_confirmacao_avisa_o_painel(client, monkeypatch):
    async def fake_get_client():
        return object()
    async def fake_noop(*args, **kwargs):
        return None
    async def boom(*args, **kwargs):
        raise eva_client.EvaUnavailable("Eva fora do ar")
    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_noop)
    monkeypatch.setattr(attendant_db, "log_event", fake_noop)
    monkeypatch.setattr(eva_client, "post", boom)

    r = _pagar(client, "taxa", 100, "João")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "confirmacao": "falhou"}


def test_pagar_eva_responde_erro_conta_como_falha(client, monkeypatch):
    async def fake_get_client():
        return object()
    async def fake_noop(*args, **kwargs):
        return None
    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_noop)
    monkeypatch.setattr(attendant_db, "log_event", fake_noop)
    _eva_recorder(monkeypatch, status=502)

    r = _pagar(client, "taxa", 100, "João")
    assert r.json()["confirmacao"] == "falhou"


def _pay_patches(monkeypatch, calls, *, resolved=None):
    async def fake_get_client():
        return object()
    async def fake_noop(*args, **kwargs):
        return None
    async def fake_resolve(phone):
        return resolved or {"contact": None, "patients": []}

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_paid", fake_noop)
    monkeypatch.setattr(attendant_db, "log_event", fake_noop)
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)
    sent = _eva_recorder(monkeypatch)
    calls["sent"] = sent


def _pagar(client, tipo, valor, paciente):
    return client.post(
        "/api/atendente/pagamentos/a1/pagar",
        params={"token": "test-token"},
        json={"tipo": tipo, "valor": valor, "forma_pagamento": "PIX",
              "paciente": paciente, "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
              "phone": "5581999998888", "conversation_id": 42},
    )


def _sent_body(calls):
    return calls["sent"][0][1]


def test_pagar_taxa_monta_template_e_texto(client, monkeypatch):
    calls = {}
    _pay_patches(monkeypatch, calls)
    r = _pagar(client, "taxa", 100, "João Silva")
    assert r.status_code == 200
    body = _sent_body(calls)
    assert body["template"] == "pagamento_taxa_recebido"
    assert body["params"] == {"1": "João", "2": "R$ 100,00", "3": "sua consulta"}
    assert "sua consulta está garantida" in body["text"]


def test_pagar_consulta_monta_template_e_texto(client, monkeypatch):
    calls = {}
    _pay_patches(monkeypatch, calls)
    _pagar(client, "consulta", 650, "João Silva")
    body = _sent_body(calls)
    assert body["template"] == "pagamento_consulta_recebido"
    assert body["params"] == {"1": "João", "2": "R$ 650,00", "3": "à sua consulta"}
    assert "R$ 650,00 referente à sua consulta" in body["text"]
    assert "Está tudo certo agora, muito obrigada!" in body["text"]


def test_pagar_terceiro_cita_o_paciente_pelo_nome(client, monkeypatch):
    calls = {}
    resolved = {
        "contact": {"id": "c1", "name": "Daniella Souza"},
        "patients": [{"id": "p1", "name": "Bento Souza", "link": {"is_self": False}}],
    }
    _pay_patches(monkeypatch, calls, resolved=resolved)
    _pagar(client, "taxa", 100, "Bento Souza")
    body = _sent_body(calls)
    assert body["params"] == {"1": "Daniella", "2": "R$ 100,00", "3": "a consulta de Bento"}
    assert "a consulta de Bento está garantida" in body["text"]


def test_pagar_proprio_paciente_usa_sua_consulta(client, monkeypatch):
    calls = {}
    resolved = {
        "contact": {"id": "c1", "name": "Ana"},
        "patients": [{"id": "p1", "name": "Ana Lima", "link": {"is_self": True}}],
    }
    _pay_patches(monkeypatch, calls, resolved=resolved)
    _pagar(client, "consulta", 1200, "Ana Lima")
    assert _sent_body(calls)["params"] == {"1": "Ana", "2": "R$ 1.200,00", "3": "à sua consulta"}


# ── Isenção de taxa de reserva ────────────────────────────────────────────────


def test_isentar_requires_token(client):
    r = client.post("/api/atendente/pagamentos/a1/isentar", json={
        "paciente": "João", "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
        "phone": "5581999998888",
    })
    assert r.status_code == 401


def _isentar_patches(monkeypatch, calls, *, eva_status=200, eva_payload=None, resolved=None):
    async def fake_get_client():
        return object()
    async def fake_mark_fee_waived(_client, appointment_id, paciente, medico, data_hora):
        calls["mark_fee_waived"] = (appointment_id, paciente, medico, data_hora)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    async def fake_post(path, body, timeout=30.0):
        calls.setdefault("sent", []).append((path, body))
        return eva_status, eva_payload if eva_payload is not None else {"sent": True, "via": "texto"}
    async def fake_resolve(phone):
        return resolved or {"contact": None, "patients": []}

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "mark_fee_waived", fake_mark_fee_waived)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)
    monkeypatch.setattr(eva_client, "post", fake_post)


def _isentar(client, paciente="João", conversation_id=42):
    body = {"paciente": paciente, "medico": "Dr. Júlio", "data_hora": "10/07/2026 14:00",
            "phone": "5581999998888"}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return client.post("/api/atendente/pagamentos/a1/isentar",
                       params={"token": "test-token"}, json=body)


def test_isentar_registra_e_envia_confirmacao_pela_eva(client, monkeypatch):
    calls = {}
    _isentar_patches(monkeypatch, calls)
    r = _isentar(client)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "confirmacao": "enviada"}
    assert calls["mark_fee_waived"] == ("a1", "João", "Dr. Júlio", "10/07/2026 14:00")
    path, body = calls["sent"][0]
    assert path == "/admin/panel/payment-confirmation"
    assert body["template"] == ""  # não há template aprovado para isenção
    assert "sua consulta com Dr. Júlio foi isentada" in body["text"]
    assert calls["log"][0] == "attendant_taxa_isentada"


def test_isentar_terceiro_cita_o_paciente(client, monkeypatch):
    calls = {}
    resolved = {
        "contact": {"id": "c1", "name": "Juliana Libonati"},
        "patients": [{"id": "p1", "name": "Bento Libonati", "link": {"is_self": False}}],
    }
    _isentar_patches(monkeypatch, calls, resolved=resolved)
    _isentar(client, paciente="Bento Libonati")
    text = calls["sent"][0][1]["text"]
    assert text.startswith("Olá, Juliana!")
    assert "a consulta de Bento com Dr. Júlio" in text


def test_isentar_sem_conversation_id_envia_mesmo_assim(client, monkeypatch):
    calls = {}
    _isentar_patches(monkeypatch, calls)
    r = _isentar(client, conversation_id=None)
    assert r.status_code == 200
    assert len(calls["sent"]) == 1


def test_isentar_fora_da_janela_avisa_o_painel(client, monkeypatch):
    calls = {}
    _isentar_patches(monkeypatch, calls, eva_payload={"sent": False, "motivo": "janela_fechada"})
    r = _isentar(client)
    assert r.json() == {"ok": True, "confirmacao": "fora_da_janela"}


def test_isentar_falha_no_envio_nao_quebra_e_avisa(client, monkeypatch):
    calls = {}
    _isentar_patches(monkeypatch, calls, eva_status=502, eva_payload={"detail": "erro"})
    r = _isentar(client)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "confirmacao": "falhou"}
    assert calls["mark_fee_waived"]


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


# ── Comprovante + registro numa única requisição ─────────────────────────────
# Caso Bento/Juliana (05/10/2026): o upload ia numa chamada e o registro em outra;
# a segunda se perdeu, o arquivo ficou no Drive e a taxa seguiu em aberto.

_FORM_PAGAR = {"tipo": "taxa", "valor": "100", "forma_pagamento": "PIX",
               "paciente": "Bento", "medico": "Dr. Júlio", "data_hora": "05/11/2026 14:00",
               "phone": "5581999713131", "conversation_id": "107"}


def _pagar_com_comprovante(client, data=None, with_file=True):
    files = {"file": ("comprovante.jpg", b"img", "image/jpeg")} if with_file else None
    return client.post(
        "/api/atendente/pagamentos/a1/pagar-com-comprovante",
        params={"token": "test-token"},
        data=data or _FORM_PAGAR,
        files=files,
    )


@pytest.fixture
def pagar_fakes(monkeypatch):
    calls = {"log": []}
    async def fake_get_client():
        return object()
    async def fake_upload(patient_name, appointment_dt, amount, file_bytes, mimetype):
        calls["upload"] = (patient_name, appointment_dt, amount, file_bytes)
        return "https://drive.google.com/file/d/xyz/view", "Bento_05-11-2026_R$100.jpg"
    async def fake_mark_paid(_client, appointment_id, tipo, valor, forma_pagamento,
                              paciente, medico, data_hora, phone, drive_link="",
                              receipt_filename=""):
        calls["mark_paid"] = (appointment_id, tipo, valor, drive_link, receipt_filename)
    async def fake_log(event_type, phone, metadata):
        calls["log"].append((event_type, metadata))

    monkeypatch.setattr(attendant_routes, "get_client", fake_get_client)
    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    calls["sent"] = _eva_recorder(monkeypatch)
    return calls


def test_pagar_com_comprovante_requires_token(client):
    r = client.post("/api/atendente/pagamentos/a1/pagar-com-comprovante", data=_FORM_PAGAR)
    assert r.status_code == 401


def test_pagar_com_comprovante_sobe_e_registra_junto(client, pagar_fakes):
    r = _pagar_com_comprovante(client)
    assert r.status_code == 200
    assert pagar_fakes["upload"] == ("Bento", "05/11/2026 14:00", "100", b"img")
    assert pagar_fakes["mark_paid"] == (
        "a1", "taxa", 100, "https://drive.google.com/file/d/xyz/view", "Bento_05-11-2026_R$100.jpg",
    )
    assert r.json() == {"ok": True, "confirmacao": "enviada"}
    assert pagar_fakes["sent"][0][1]["phone"] == "5581999713131"
    assert pagar_fakes["log"][-1][0] == "attendant_pagamento_registrado"


def test_pagar_com_comprovante_sem_arquivo_registra_sem_link(client, pagar_fakes):
    r = _pagar_com_comprovante(client, with_file=False)
    assert r.status_code == 200
    assert "upload" not in pagar_fakes
    assert pagar_fakes["mark_paid"][3:] == ("", "")


def test_pagar_com_comprovante_falha_no_drive_nao_registra(client, pagar_fakes, monkeypatch):
    async def fake_upload(*a, **k):
        raise RuntimeError("Drive fora")
    monkeypatch.setattr(payments, "upload_comprovante", fake_upload)
    r = _pagar_com_comprovante(client)
    assert r.status_code == 502
    assert "Nada foi registrado" in r.json()["detail"]
    assert "mark_paid" not in pagar_fakes


def test_pagar_com_comprovante_falha_no_registro_avisa_e_loga(client, pagar_fakes, monkeypatch):
    async def fake_mark_paid(*a, **k):
        raise RuntimeError("Supabase fora")
    monkeypatch.setattr(payments, "mark_paid", fake_mark_paid)
    r = _pagar_com_comprovante(client)
    assert r.status_code == 500
    assert "comprovante foi salvo" in r.json()["detail"]
    assert "NÃO foi registrado" in r.json()["detail"]
    event, meta = pagar_fakes["log"][-1]
    assert event == "attendant_pagamento_falhou"
    assert meta["drive_link"] == "https://drive.google.com/file/d/xyz/view"


def test_pagar_com_comprovante_falha_so_no_log_ainda_responde_ok(client, pagar_fakes, monkeypatch):
    """Pagamento já gravado: erro só no log não pode virar 'NÃO registrado',
    senão a atendente tenta de novo e duplica a linha da planilha."""
    async def fake_log(*a, **k):
        raise RuntimeError("events fora")
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = _pagar_com_comprovante(client)
    assert r.status_code == 200
    assert "mark_paid" in pagar_fakes


def test_pagar_com_comprovante_tipo_invalido_nao_sobe_arquivo(client, pagar_fakes):
    r = _pagar_com_comprovante(client, data={**_FORM_PAGAR, "tipo": "outro"})
    assert r.status_code == 400
    assert "upload" not in pagar_fakes


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


# ── Consultas ─────────────────────────────────────────────────────────────────


def test_consultas_returns_list(client, monkeypatch):
    async def fake(pid):
        return {"appointments": [], "pending_part2": None, "has_completed": False, "pid": pid}
    monkeypatch.setattr(attendant_db, "list_consultas", fake)
    r = client.get("/api/atendente/consultas", params={"token": "test-token", "phone": "5581", "patient_id": "p1"})
    assert r.status_code == 200 and r.json()["pid"] == "p1"


def test_first_consultation_toggle(client, monkeypatch):
    calls = {}
    async def fake_doctor_id(aid):
        return "d5baa58b-a788-4f40-b8c0-512c189150be"  # Dr. Júlio
    async def fake_set(aid, first):
        calls["set"] = (aid, first)
    async def fake_log(t, phone, meta):
        calls["log"] = t
    monkeypatch.setattr(attendant_db, "get_appointment_doctor_id", fake_doctor_id)
    monkeypatch.setattr(attendant_db, "set_first_consultation", fake_set)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/consulta/a1/primeira", params={"token": "test-token"},
                    json={"phone": "5581", "first": False, "agent": "Maria"})
    assert r.status_code == 200
    assert calls == {"set": ("a1", False), "log": "attendant_first_consultation"}


def test_first_consultation_toggle_rejects_non_julio_doctor(client, monkeypatch):
    async def fake_doctor_id(aid):
        return "18b01f87-eacd-4905-bd4a-a8293991e6fd"  # Dra. Bruna
    monkeypatch.setattr(attendant_db, "get_appointment_doctor_id", fake_doctor_id)
    r = client.post("/api/atendente/consulta/a1/primeira", params={"token": "test-token"},
                    json={"phone": "5581", "first": True, "agent": "Maria"})
    assert r.status_code == 400
    assert "Dr. Júlio" in r.json()["detail"]


def test_nova_consulta_forwards_to_eva(client, monkeypatch):
    seen = {}
    async def fake_post(path, body, timeout=None):
        seen["path"], seen["body"], seen["timeout"] = path, body, timeout
        return 200, {"appointments": [{"appointment_id": "e1"}], "message": {"sent": ["Ana"]}}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    body = {"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "presencial",
            "parts": [{"start": "2026-10-05T09:00", "minutes": 60}], "billing": "normal", "agent": "Maria"}
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"}, json=body)
    assert r.status_code == 200
    assert seen["path"] == "/admin/panel/appointments"
    assert seen["body"]["patient_id"] == "p1" and seen["body"]["phone"] == "5581"
    assert seen["log"] == "attendant_new_appointment"
    assert seen["timeout"] == attendant_routes._BOOKING_TIMEOUT


def test_nova_consulta_dry_run_uses_short_timeout(client, monkeypatch):
    seen = {}
    async def fake_post(path, body, timeout=None):
        seen["timeout"] = timeout
        return 200, {"encaixe_reasons": [], "message": {}}
    monkeypatch.setattr(eva_client, "post", fake_post)
    body = {"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "presencial",
            "parts": [{"start": "2026-10-05T09:00", "minutes": 60}], "dry_run": True}
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"}, json=body)
    assert r.status_code == 200
    assert seen["timeout"] == attendant_routes._DRY_RUN_TIMEOUT


def test_nova_consulta_passes_409(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        return 409, {"detail": {"needs_encaixe": True, "reasons": ["dia bloqueado na agenda"]}}
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 409
    assert r.json()["detail"]["reasons"] == ["dia bloqueado na agenda"]


def test_nova_consulta_eva_down(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        raise eva_client.EvaUnavailable("down")
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 503
    assert "Nada foi alterado" in r.json()["detail"]


def test_nova_consulta_eva_timeout_not_dry_run_returns_504(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        raise eva_client.EvaTimeout("timed out")
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 504
    assert "demorou a responder" in r.json()["detail"]


def test_nova_consulta_eva_timeout_dry_run_returns_503(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        raise eva_client.EvaTimeout("timed out")
    monkeypatch.setattr(eva_client, "post", fake_post)
    body = {"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
            "parts": [{"start": "2026-10-05T09:00", "minutes": 60}], "dry_run": True}
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"}, json=body)
    assert r.status_code == 503
    assert "Nada foi alterado" in r.json()["detail"]


def test_nova_consulta_eva_forbidden_returns_503_friendly(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        return 403, {"detail": "Forbidden"}
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 503
    assert r.json()["detail"] == "Painel sem acesso à Eva (configuração). Nada foi alterado."


def test_nova_consulta_eva_unauthorized_returns_503_friendly(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        return 401, {"detail": "Unauthorized"}
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 503


# ── Alterar / cancelar consulta ──────────────────────────────────────────────


def test_alterar_forwards_with_appointment_id(client, monkeypatch):
    seen = {}
    async def fake_post(path, body, timeout=None):
        seen["path"], seen["body"], seen["timeout"] = path, body, timeout
        return 200, {"appointment_id": "a1", "warnings": [], "message": {"sent": ["Ana"]}}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    body = {"phone": "5581", "doctor": "julio", "modality": "online", "start": "2026-10-06T09:00",
            "minutes": 60, "initiated_by": "clinic", "agent": "Maria"}
    r = client.post("/api/atendente/consulta/a1/alterar", params={"token": "test-token"}, json=body)
    assert r.status_code == 200
    assert seen["path"] == "/admin/panel/appointments/edit"
    assert seen["body"]["appointment_id"] == "a1" and seen["body"]["initiated_by"] == "clinic"
    assert seen["log"] == "attendant_edit_appointment"
    assert seen["timeout"] == attendant_routes._BOOKING_TIMEOUT


def test_cancelar_forwards_and_logs(client, monkeypatch):
    seen = {}
    async def fake_post(path, body, timeout=None):
        seen["path"], seen["body"] = path, body
        return 200, {"canceled": ["a1"], "warnings": [], "message": {"sent": []}}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/consulta/a1/cancelar", params={"token": "test-token"},
                    json={"phone": "5581", "initiated_by": "patient", "fee_action": "reter"})
    assert r.status_code == 200
    assert seen["path"] == "/admin/panel/appointments/cancel"
    assert seen["body"]["appointment_id"] == "a1" and seen["body"]["fee_action"] == "reter"
    assert seen["log"] == "attendant_cancel_appointment"


def test_cancelar_dry_run_does_not_log(client, monkeypatch):
    seen = {"log": None}
    async def fake_post(path, body, timeout=None):
        seen["timeout"] = timeout
        return 200, {"fee_paid": False, "message": None}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/consulta/a1/cancelar", params={"token": "test-token"},
                    json={"phone": "5581", "dry_run": True})
    assert r.status_code == 200 and seen["log"] is None
    assert seen["timeout"] == attendant_routes._DRY_RUN_TIMEOUT


def test_cancelar_eva_timeout_504(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        raise eva_client.EvaTimeout("timed out")
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/a1/cancelar", params={"token": "test-token"},
                    json={"phone": "5581", "initiated_by": "clinic"})
    assert r.status_code == 504 and "Confira a lista" in r.json()["detail"]
