import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
import base64
import sys
import os

# Patch Jinja2Templates before importing the dashboard app
with patch("fastapi.templating.Jinja2Templates"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    from main import app

AUTH = base64.b64encode(b"admin:changeme").decode()
HEADERS = {"Authorization": f"Basic {AUTH}"}


@pytest.fixture
def mock_supabase():
    with patch("main.get_supabase") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.mark.asyncio
async def test_pagamentos_page_returns_html(mock_supabase):
    """GET /pagamentos deve retornar 200 com HTML."""
    from fastapi.responses import HTMLResponse
    mock_supabase.from_.return_value.select.return_value.in_.return_value\
        .execute = AsyncMock(return_value=MagicMock(data=[]))

    html_content = "<html><body>Pagamentos Pendentes</body></html>"
    import main as dashboard_main
    dashboard_main.templates.TemplateResponse.return_value = HTMLResponse(content=html_content)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/pagamentos", headers=HEADERS)
    assert resp.status_code == 200
    assert "Pagamentos Pendentes" in resp.text


@pytest.mark.asyncio
async def test_pagamentos_sem_auth_retorna_401(mock_supabase):
    """GET /pagamentos sem credenciais retorna 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/pagamentos")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pagar_taxa_atualiza_booking_fee(mock_supabase):
    """POST /api/pagamentos/{id}/pagar com tipo=taxa atualiza booking_fee_paid_at."""
    update_mock = AsyncMock(return_value=AsyncMock(data=[{}]))
    mock_supabase.from_.return_value.update.return_value.eq.return_value.execute = update_mock

    with patch("payments._append_payment_sheet", new_callable=AsyncMock) as mock_sheets, \
         patch("payments._send_clinic_email", new_callable=AsyncMock) as mock_email:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/pagamentos/appt-123/pagar",
                json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
                      "paciente": "João Silva", "medico": "Dr. Júlio",
                      "data_hora": "25/06/2026 14:00", "phone": "5511999990000"},
                headers=HEADERS,
            )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    update_call = mock_supabase.from_.return_value.update.call_args
    payload = update_call[0][0]
    assert "booking_fee_paid_at" in payload
    assert "paid_at" not in payload
    mock_sheets.assert_called_once()
    mock_email.assert_called_once()


@pytest.mark.asyncio
async def test_pagar_consulta_atualiza_paid_at(mock_supabase):
    """POST /api/pagamentos/{id}/pagar com tipo=consulta atualiza paid_at."""
    update_mock = AsyncMock(return_value=AsyncMock(data=[{}]))
    mock_supabase.from_.return_value.update.return_value.eq.return_value.execute = update_mock

    with patch("payments._append_payment_sheet", new_callable=AsyncMock) as mock_sheets, \
         patch("payments._send_clinic_email", new_callable=AsyncMock) as mock_email:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/pagamentos/appt-456/pagar",
                json={"tipo": "consulta", "valor": 650, "forma_pagamento": "cartao_credito",
                      "paciente": "Maria Costa", "medico": "Dra. Bruna",
                      "data_hora": "26/06/2026 10:00", "phone": "5511988880000"},
                headers=HEADERS,
            )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    update_call = mock_supabase.from_.return_value.update.call_args
    payload = update_call[0][0]
    assert "paid_at" in payload
    assert "booking_fee_paid_at" not in payload
    mock_sheets.assert_called_once()
    mock_email.assert_called_once()


@pytest.mark.asyncio
async def test_pagar_repassa_nome_do_comprovante_ate_a_planilha(mock_supabase):
    """O nome real do arquivo no Drive percorre rota → mark_paid → planilha.

    A extensão só é conhecida no upload (vem do mimetype), e upload e pagamento são
    duas requisições separadas — sem esse repasse a planilha remontava o nome por
    conta própria e exibia um nome que arquivo nenhum do Drive tinha.
    """
    update_mock = AsyncMock(return_value=AsyncMock(data=[{}]))
    mock_supabase.from_.return_value.update.return_value.eq.return_value.execute = update_mock
    mock_supabase.from_.return_value.insert.return_value.execute = AsyncMock(
        return_value=AsyncMock(data=[{}])
    )

    with patch("payments._append_payment_sheet", new_callable=AsyncMock) as mock_sheets, \
         patch("payments._send_clinic_email", new_callable=AsyncMock), \
         patch("attendant_db.log_event", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/pagamentos/appt-321/pagar",
                json={"tipo": "consulta", "valor": 550, "forma_pagamento": "PIX",
                      "paciente": "João Silva", "medico": "Dr. Júlio",
                      "data_hora": "10/07/2026 14:00", "phone": "5511999990000",
                      "drive_link": "https://drive.google.com/file/d/abc123/view",
                      "receipt_filename": "João_Silva_10-07-2026_R$550.pdf"},
                headers=HEADERS,
            )
    assert resp.status_code == 200
    assert mock_sheets.call_args.kwargs["receipt_filename"] == "João_Silva_10-07-2026_R$550.pdf"


@pytest.mark.asyncio
async def test_pagar_tipo_invalido_retorna_400():
    """POST /api/pagamentos/{id}/pagar com tipo desconhecido retorna 400."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/pagamentos/appt-789/pagar",
            json={"tipo": "invalido", "valor": 100, "forma_pagamento": "PIX",
                  "paciente": "X", "medico": "Y", "data_hora": "01/01/2026 10:00", "phone": ""},
            headers=HEADERS,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_sheets_append_failure_notifies_clinic(mock_supabase):
    """POST /api/pagamentos/{id}/pagar envia alerta de e-mail quando sheets append falha."""
    update_mock = AsyncMock(return_value=AsyncMock(data=[{}]))
    mock_supabase.from_.return_value.update.return_value.eq.return_value.execute = update_mock

    async def failing_append(*args, **kwargs):
        raise RuntimeError("Google Sheets append retornou updatedRange vazio — pagamento NÃO foi gravado.")

    with patch("payments._append_payment_sheet", side_effect=failing_append), \
         patch("payments._send_clinic_email", new_callable=AsyncMock) as mock_email:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/pagamentos/appt-123/pagar",
                json={"tipo": "taxa", "valor": 100, "forma_pagamento": "PIX",
                      "paciente": "Camila Brasileiro", "medico": "Dr. Júlio",
                      "data_hora": "27/07/2026 14:00", "phone": "5581987516312"},
                headers=HEADERS,
            )
    assert resp.status_code == 200
    # Ensure the alert email was called with failure message
    alert_emails = [call for call in mock_email.call_args_list
                    if "FALHA ao gravar" in str(call)]
    assert len(alert_emails) > 0, "Clinic should be notified of sheets append failure"


# ── E-mail à clínica: falha nunca some em silêncio ────────────────────────────

@pytest.mark.asyncio
async def test_send_clinic_email_sem_smtp_registra_evento_e_levanta():
    """SMTP ausente no serviço dashboard vira clinic_email_failed + exceção.

    Regressão: o dashboard roda em container separado do bot; sem SMTP a função
    apenas retornava, e o pagamento ficava registrado sem a clínica saber
    (Arthur Tenório e Camila Brasileiro, 27/07/2026).
    """
    import payments

    with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASSWORD": "",
                                 "CLINIC_NOTIFY_EMAIL": ""}, clear=False), \
         patch("attendant_db.log_event", new_callable=AsyncMock) as mock_log:
        with pytest.raises(RuntimeError):
            await payments._send_clinic_email(
                subject="Comprovante recebido — Arthur", body="corpo", phone="5581996503841",
            )

    mock_log.assert_awaited_once()
    event_type, phone, metadata = mock_log.await_args.args
    assert event_type == "clinic_email_failed"
    assert phone == "5581996503841"
    assert metadata["origin"] == "dashboard"
    assert "CLINIC_NOTIFY_EMAIL" in metadata["reason"]


@pytest.mark.asyncio
async def test_send_clinic_email_falha_de_envio_registra_evento():
    """Erro de SMTP no envio também vira clinic_email_failed."""
    import payments

    env = {"SMTP_HOST": "smtp.test", "SMTP_PORT": "465", "SMTP_USER": "u@test",
           "SMTP_PASSWORD": "x", "CLINIC_NOTIFY_EMAIL": "clinica@test"}
    with patch.dict(os.environ, env, clear=False), \
         patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")), \
         patch("attendant_db.log_event", new_callable=AsyncMock) as mock_log:
        with pytest.raises(OSError):
            await payments._send_clinic_email(subject="Assunto", body="corpo", phone="5581999999999")

    mock_log.assert_awaited_once()
    event_type, _, metadata = mock_log.await_args.args
    assert event_type == "clinic_email_failed"
    assert "connection refused" in metadata["reason"]


@pytest.mark.asyncio
async def test_pagar_sem_smtp_registra_pagamento_e_falha_de_email(mock_supabase):
    """Pagamento continua sendo registrado mesmo sem SMTP — mas a falha fica auditável."""
    update_mock = AsyncMock(return_value=AsyncMock(data=[{}]))
    mock_supabase.from_.return_value.update.return_value.eq.return_value.execute = update_mock

    with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASSWORD": "",
                                 "CLINIC_NOTIFY_EMAIL": ""}, clear=False), \
         patch("payments._append_payment_sheet", new_callable=AsyncMock), \
         patch("attendant_db.log_event", new_callable=AsyncMock) as mock_log:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/pagamentos/appt-123/pagar",
                json={"tipo": "consulta", "valor": 550, "forma_pagamento": "PIX",
                      "paciente": "Arthur Tenório Ribeiro Clark", "medico": "Dra. Bruna",
                      "data_hora": "27/07/2026 16:00", "phone": "5581996503841"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    update_mock.assert_awaited()
    logged = [c.args[0] for c in mock_log.await_args_list]
    assert "clinic_email_failed" in logged


def test_missing_smtp_vars_lista_apenas_ausentes():
    """missing_smtp_vars alimenta o aviso de startup do dashboard."""
    import payments

    env = {"SMTP_HOST": "smtp.test", "SMTP_USER": "u@test",
           "SMTP_PASSWORD": "", "CLINIC_NOTIFY_EMAIL": ""}
    with patch.dict(os.environ, env, clear=False):
        assert payments.missing_smtp_vars() == ["SMTP_PASSWORD", "CLINIC_NOTIFY_EMAIL"]

    env_ok = {"SMTP_HOST": "smtp.test", "SMTP_USER": "u@test",
              "SMTP_PASSWORD": "x", "CLINIC_NOTIFY_EMAIL": "clinica@test"}
    with patch.dict(os.environ, env_ok, clear=False):
        assert payments.missing_smtp_vars() == []
