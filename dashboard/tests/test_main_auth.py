"""Auth das rotas do app principal do dashboard.

Cobre dois buracos fechados:
  1. GET /atendente entregava o ATTENDANT_PANEL_TOKEN a qualquer visitante sem
     validar o token da query. Agora exige o token (o Chatwoot já o envia na URL
     do iframe), então o embed continua funcionando e o anônimo leva 401.
  2. O WebSocket /ws transmitia mensagens de todos os pacientes sem autenticação.
     Agora exige a mesma credencial HTTP Basic do resto do dashboard (o navegador
     já a envia no handshake porque a página / foi aberta autenticada).
"""
import base64

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main as dashboard_main

PANEL_TOKEN = "test-token"  # setado em conftest via ATTENDANT_PANEL_TOKEN
BASIC_PW = "s3nha-teste"    # DASHBOARD_PASSWORD do conftest


def _client():
    return TestClient(dashboard_main.app)


def _basic_header(user: str, password: str) -> str:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {raw}"


# ── /atendente (achado 1) ──────────────────────────────────────────────────────

def test_atendente_sem_token_recusa():
    r = _client().get("/atendente")
    assert r.status_code == 401
    assert PANEL_TOKEN not in r.text  # o segredo não vaza no corpo


def test_atendente_token_errado_recusa():
    r = _client().get("/atendente", params={"token": "errado"})
    assert r.status_code == 401
    assert PANEL_TOKEN not in r.text


def test_atendente_token_certo_renderiza_para_o_chatwoot():
    r = _client().get("/atendente", params={"token": PANEL_TOKEN})
    assert r.status_code == 200
    # O embed do Chatwoot precisa do token injetado no JS pra chamar a API.
    assert PANEL_TOKEN in r.text


# ── /ws (achado 2) ─────────────────────────────────────────────────────────────

def test_ws_sem_auth_recusa():
    with pytest.raises(WebSocketDisconnect):
        with _client().websocket_connect("/ws"):
            pass


def test_ws_senha_errada_recusa():
    headers = {"Authorization": _basic_header("user", "senha-errada")}
    with pytest.raises(WebSocketDisconnect):
        with _client().websocket_connect("/ws", headers=headers):
            pass


def test_ws_auth_correta_conecta():
    headers = {"Authorization": _basic_header("user", BASIC_PW)}
    with _client().websocket_connect("/ws", headers=headers) as ws:
        # Conectou sem ser derrubado; a conexão está viva.
        assert ws is not None


# ── Senha fail-closed (achado: DASHBOARD_PASSWORD default "changeme") ───────────

@pytest.mark.parametrize("insecure", ["", "changeme"])
def test_senha_insegura_recusa_index(monkeypatch, insecure):
    """Com senha vazia ou 'changeme' o painel recusa até a própria senha."""
    monkeypatch.setattr(dashboard_main, "DASHBOARD_PASSWORD", insecure)
    r = _client().get("/", auth=("user", insecure))
    assert r.status_code == 401


@pytest.mark.parametrize("insecure", ["", "changeme"])
def test_senha_insegura_recusa_ws(monkeypatch, insecure):
    monkeypatch.setattr(dashboard_main, "DASHBOARD_PASSWORD", insecure)
    headers = {"Authorization": _basic_header("user", insecure)}
    with pytest.raises(WebSocketDisconnect):
        with _client().websocket_connect("/ws", headers=headers):
            pass


# ── Cabeçalhos de segurança ────────────────────────────────────────────────────

def test_cabecalhos_de_seguranca_presentes():
    r = _client().get("/atendente", params={"token": PANEL_TOKEN})
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "max-age=" in r.headers["Strict-Transport-Security"]
    # O Chatwoot precisa continuar podendo embutir o painel no iframe.
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]
    assert "x-frame-options" not in {k.lower() for k in r.headers}


# ── Moldura nova + lista de parentesco ──────────────────────────────────────────

def test_atendente_renderiza_moldura_e_lista_de_parentesco():
    r = _client().get("/atendente", params={"token": PANEL_TOKEN})
    assert r.status_code == 200
    for marca in ('id="contact-name"', 'id="patient-box"', 'id="link-sheet"',
                  'data-tab="financeiro"', 'data-tab="cadastro"', 'data-tab="reset"',
                  '<option value="acompanhante">acompanhante</option>',
                  '<option value="tutor(a)">tutor(a)</option>'):
        assert marca in r.text, marca
    assert 'data-tab="contato"' not in r.text


# ── Aba Consultas (Parte 2) ─────────────────────────────────────────────────────

def test_atendente_page_has_consultas_tab_first():
    r = _client().get("/atendente", params={"token": PANEL_TOKEN})
    assert r.status_code == 200
    html = r.text
    assert 'data-tab="consultas"' in html
    assert html.index('data-tab="consultas"') < html.index('data-tab="financeiro"')
    assert 'id="tab-consultas"' in html
    # A aba Consultas abre selecionada e o Financeiro começa escondido.
    assert '<button type="button" role="tab" data-tab="consultas" class="tab" aria-selected="true">' in html
    assert 'id="tab-financeiro" class="tab-panel hidden' in html
    assert 'id="appt-sheet"' in html
