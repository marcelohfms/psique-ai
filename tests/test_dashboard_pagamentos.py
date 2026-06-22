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
        client = AsyncMock()
        mock.return_value = client
        yield client


@pytest.mark.asyncio
async def test_pagamentos_page_returns_html(mock_supabase):
    """GET /pagamentos deve retornar 200 com HTML."""
    mock_supabase.from_.return_value.select.return_value.in_.return_value\
        .execute = AsyncMock(return_value=AsyncMock(data=[]))

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

    with patch("main.append_payment_receipt", new_callable=AsyncMock, create=True) as mock_sheets, \
         patch("main.send_clinic_notification_email", new_callable=AsyncMock, create=True) as mock_email:
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

    with patch("main.append_payment_receipt", new_callable=AsyncMock, create=True) as mock_sheets, \
         patch("main.send_clinic_notification_email", new_callable=AsyncMock, create=True) as mock_email:
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
