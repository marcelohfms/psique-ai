# Pagamentos Pendentes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar a rota `/pagamentos` no dashboard existente, mostrando consultas com taxa de reserva ou consulta não pagas, com botão para marcar como pago — e ao fazer isso, preencher a planilha de pagamentos e enviar e-mail de notificação para a clínica.

**Architecture:** Nova rota `GET /pagamentos` renderiza template HTML com tabela de pendências. `POST /api/pagamentos/{appointment_id}/pagar` recebe tipo (`taxa`/`consulta`), valor e forma de pagamento, atualiza o Supabase, preenche o Google Sheets via `app.google_sheets.append_payment_receipt` e envia e-mail via `app.email_sender.send_clinic_notification_email`. O dashboard importa o pacote `psique-chatbot` (app principal) como dependência local para reutilizar essas funções.

**Tech Stack:** FastAPI, Jinja2, Tailwind CSS (CDN), Supabase, Google Sheets API, SMTP — tudo via pacote `app/` já existente.

---

## Arquivos

- **Modificar:** `dashboard/pyproject.toml` — adicionar `psique-chatbot` como dependência local
- **Modificar:** `dashboard/main.py` — adicionar constantes, função de cálculo de valor e 2 novas rotas
- **Criar:** `dashboard/templates/pagamentos.html` — template da tabela de pendências
- **Criar:** `tests/test_dashboard_pagamentos.py` — testes dos novos endpoints

---

### Task 1: Adicionar dependência do pacote principal ao dashboard

**Files:**
- Modify: `dashboard/pyproject.toml`

- [ ] **Passo 1: Adicionar psique-chatbot como dependência local**

Editar `dashboard/pyproject.toml`:

```toml
[project]
name = "psique-dashboard"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "jinja2>=3.1.4",
    "python-dotenv>=1.0.1",
    "supabase>=2.10.0",
    "python-multipart>=0.0.18",
    "psique-chatbot",
]

[tool.uv.sources]
psique-chatbot = { path = "..", editable = true }
```

- [ ] **Passo 2: Instalar a dependência no ambiente do dashboard**

```bash
cd /Users/ayexatavares/psique-ai/dashboard
uv sync
```

Esperado: instala sem erros.

- [ ] **Passo 3: Confirmar que o import funciona**

```bash
cd /Users/ayexatavares/psique-ai/dashboard
uv run python -c "from app.google_sheets import append_payment_receipt; from app.email_sender import send_clinic_notification_email; print('ok')"
```

Esperado: `ok`

---

### Task 2: Testes dos endpoints (TDD)

**Files:**
- Create: `tests/test_dashboard_pagamentos.py`

- [ ] **Passo 1: Criar o arquivo de testes**

```python
# tests/test_dashboard_pagamentos.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
import base64
import sys
import os

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

    with patch("main.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("main.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
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

    with patch("main.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("main.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
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
```

- [ ] **Passo 2: Rodar os testes e confirmar que falham**

```bash
cd /Users/ayexatavares/psique-ai
uv run pytest tests/test_dashboard_pagamentos.py -v
```

Esperado: falhas com `404` ou `ImportError` (rotas ainda não existem).

---

### Task 3: Função auxiliar de cálculo de valor e constantes

**Files:**
- Modify: `dashboard/main.py`

- [ ] **Passo 1: Adicionar imports e constantes após os imports existentes no topo do arquivo**

No topo de `dashboard/main.py`, após `from fastapi.templating import Jinja2Templates`, adicionar:

```python
from app.google_sheets import append_payment_receipt
from app.email_sender import send_clinic_notification_email
```

- [ ] **Passo 2: Adicionar constantes e função de cálculo após `manager = ConnectionManager()`**

```python
# ── Pagamentos ────────────────────────────────────────────────────────────────

DOCTOR_DISPLAY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}

DOCTOR_KEY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}

FORMA_PAGAMENTO_LABEL = {
    "PIX": "PIX",
    "cartao_credito": "Cartão de crédito",
    "cartao_debito": "Cartão de débito",
    "dinheiro": "Dinheiro",
}


def _calc_valor_consulta(
    doctor_id: str,
    birth_date: str | None,
    consultation_type: str | None,
    custom_price: int | None,
) -> int:
    """Retorna o valor sugerido da consulta (com desconto PIX de R$50)."""
    if custom_price is not None:
        return custom_price

    from datetime import date
    age = None
    if birth_date:
        try:
            bd = date.fromisoformat(birth_date)
            today = date.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except ValueError:
            pass

    doctor_key = DOCTOR_KEY.get(doctor_id, "")
    post_june = (date.today().year, date.today().month) >= (2026, 6)

    if doctor_key == "bruna":
        base = 700 if post_june else 600
    elif doctor_key == "julio":
        if age is None or age >= 18:
            base = 700 if post_june else 600
        elif consultation_type == "primeira_consulta":
            base = 850 if post_june else 750
        else:
            base = 750 if post_june else 650
    else:
        base = 700 if post_june else 600

    return base - 50  # desconto PIX/dinheiro
```

- [ ] **Passo 3: Verificar que o arquivo carrega sem erros**

```bash
cd /Users/ayexatavares/psique-ai/dashboard
uv run python -c "from main import app, _calc_valor_consulta; print('ok')"
```

Esperado: `ok`

---

### Task 4: Endpoints GET /pagamentos e POST /api/pagamentos/{id}/pagar

**Files:**
- Modify: `dashboard/main.py`

- [ ] **Passo 1: Adicionar a rota GET /pagamentos ao final de `dashboard/main.py` (antes do endpoint WebSocket)**

```python
@app.get("/pagamentos")
async def pagamentos_page(request: Request, username: str = Depends(verify_credentials)):
    client = await get_supabase()

    result = await (
        client.from_("appointments")
        .select(
            "appointment_id, start_time, doctor_id, paid_at, "
            "booking_fee_paid_at, booking_fee_waived, consultation_type, status, "
            "users(patient_name, name, birth_date, custom_price, number)"
        )
        .in_("status", ["scheduled", "completed"])
        .execute()
    )

    pendencias = []
    for appt in result.data or []:
        user = appt.get("users") or {}
        patient_name = user.get("patient_name") or user.get("name") or "Paciente"
        phone = user.get("number") or ""
        doctor_display = DOCTOR_DISPLAY.get(appt.get("doctor_id", ""), "Médico")
        start_time = appt.get("start_time", "")
        try:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            dt_br = dt.astimezone(timezone(timedelta(hours=-3)))
            data_hora = dt_br.strftime("%d/%m/%Y %H:%M")
        except Exception:
            data_hora = start_time[:16]

        if not appt.get("booking_fee_paid_at") and not appt.get("booking_fee_waived"):
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "tipo": "taxa",
                "tipo_label": "Taxa de reserva",
                "valor": 100,
            })

        if not appt.get("paid_at"):
            valor = _calc_valor_consulta(
                appt.get("doctor_id", ""),
                user.get("birth_date"),
                appt.get("consultation_type"),
                user.get("custom_price"),
            )
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "tipo": "consulta",
                "tipo_label": "Consulta",
                "valor": valor,
            })

    pendencias.sort(key=lambda x: x["data_hora"])
    return templates.TemplateResponse(
        request, "pagamentos.html", {"username": username, "pendencias": pendencias}
    )
```

- [ ] **Passo 2: Adicionar o endpoint POST /api/pagamentos/{id}/pagar**

```python
from pydantic import BaseModel


class PagarBody(BaseModel):
    tipo: str            # "taxa" ou "consulta"
    valor: int
    forma_pagamento: str # "PIX", "cartao_credito", "cartao_debito", "dinheiro"
    paciente: str
    medico: str
    data_hora: str
    phone: str


@app.post("/api/pagamentos/{appointment_id}/pagar")
async def api_pagar(
    appointment_id: str,
    body: PagarBody,
    username: str = Depends(verify_credentials),
):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    client = await get_supabase()
    if body.tipo == "taxa":
        await client.from_("appointments").update({"booking_fee_paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "taxa_reserva"
    else:
        await client.from_("appointments").update({"paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "consulta"

    forma_label = FORMA_PAGAMENTO_LABEL.get(body.forma_pagamento, body.forma_pagamento)
    amount_str = str(body.valor)

    try:
        await append_payment_receipt(
            patient_name=body.paciente,
            phone=body.phone,
            doctor_name=body.medico,
            appointment_dt=body.data_hora,
            amount=amount_str,
            drive_link="",
            payment_type=payment_type,
            payment_method_override=body.forma_pagamento,
        )
    except Exception:
        logger.exception("SHEETS_APPEND FAILED patient=%s", body.paciente)

    try:
        tipo_label = "Taxa de reserva" if body.tipo == "taxa" else "Consulta"
        await send_clinic_notification_email(
            subject=f"Pagamento registrado — {body.paciente}",
            body=(
                f"💰 Pagamento registrado pelo dashboard\n"
                f"Paciente: {body.paciente}\n"
                f"Médico: {body.medico}\n"
                f"Consulta: {body.data_hora}\n"
                f"Tipo: {tipo_label}\n"
                f"Valor: R$ {amount_str}\n"
                f"Forma: {forma_label}"
            ),
        )
    except Exception:
        logger.exception("EMAIL_FAILED patient=%s", body.paciente)

    return {"ok": True}
```

- [ ] **Passo 3: Verificar que o app carrega sem erros**

```bash
cd /Users/ayexatavares/psique-ai/dashboard
uv run python -c "from main import app; print('ok')"
```

Esperado: `ok`

---

### Task 5: Template HTML da página de pagamentos

**Files:**
- Create: `dashboard/templates/pagamentos.html`

- [ ] **Passo 1: Criar o template**

```html
{% extends "base.html" %}
{% block content %}
<div class="min-h-screen bg-gray-100 p-6">
  <div class="max-w-6xl mx-auto">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-semibold text-gray-800">Pagamentos Pendentes</h1>
      <a href="/" class="text-sm text-gray-500 hover:text-gray-700">← Conversas</a>
    </div>

    {% if not pendencias %}
    <div class="bg-white rounded-lg shadow p-8 text-center text-gray-500">
      Nenhum pagamento pendente 🎉
    </div>
    {% else %}
    <div class="bg-white rounded-lg shadow overflow-hidden">
      <table class="w-full text-sm" id="tabela-pendencias">
        <thead class="bg-gray-50 text-gray-500 uppercase text-xs">
          <tr>
            <th class="px-4 py-3 text-left">Paciente</th>
            <th class="px-4 py-3 text-left">Médico</th>
            <th class="px-4 py-3 text-left">Data e hora</th>
            <th class="px-4 py-3 text-left">Tipo</th>
            <th class="px-4 py-3 text-left">Valor (R$)</th>
            <th class="px-4 py-3 text-left">Forma de pagamento</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {% for p in pendencias %}
          <tr id="row-{{ p.appointment_id }}-{{ p.tipo }}" class="hover:bg-gray-50"
              data-paciente="{{ p.paciente }}"
              data-phone="{{ p.phone }}"
              data-medico="{{ p.medico }}"
              data-data-hora="{{ p.data_hora }}">
            <td class="px-4 py-3 font-medium text-gray-800">{{ p.paciente }}</td>
            <td class="px-4 py-3 text-gray-600">{{ p.medico }}</td>
            <td class="px-4 py-3 text-gray-600">{{ p.data_hora }}</td>
            <td class="px-4 py-3">
              {% if p.tipo == "taxa" %}
              <span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">Taxa de reserva</span>
              {% else %}
              <span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">Consulta</span>
              {% endif %}
            </td>
            <td class="px-4 py-3">
              <input
                type="number"
                value="{{ p.valor }}"
                min="0"
                class="valor-input w-20 border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-green-400"
              />
            </td>
            <td class="px-4 py-3">
              <select class="forma-input border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-green-400">
                <option value="PIX">PIX</option>
                <option value="cartao_credito">Cartão de crédito</option>
                <option value="cartao_debito">Cartão de débito</option>
                <option value="dinheiro">Dinheiro</option>
              </select>
            </td>
            <td class="px-4 py-3 text-right">
              <button
                onclick="marcarPago('{{ p.appointment_id }}', '{{ p.tipo }}', this)"
                class="bg-green-600 hover:bg-green-700 text-white text-xs font-medium px-3 py-1.5 rounded transition-colors disabled:opacity-50"
              >
                Pago
              </button>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <div class="px-4 py-3 bg-gray-50 text-xs text-gray-400 text-right">
        <span id="contagem">{{ pendencias|length }}</span> pendência(s)
      </div>
    </div>
    {% endif %}
  </div>
</div>

<script>
async function marcarPago(appointmentId, tipo, btn) {
  const row = document.getElementById(`row-${appointmentId}-${tipo}`);
  const valor = parseInt(row.querySelector('.valor-input').value, 10);
  const forma_pagamento = row.querySelector('.forma-input').value;

  if (isNaN(valor) || valor < 0) {
    alert('Valor inválido.');
    return;
  }

  btn.disabled = true;
  btn.textContent = '...';

  try {
    const resp = await fetch(`/api/pagamentos/${appointmentId}/pagar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tipo,
        valor,
        forma_pagamento,
        paciente: row.dataset.paciente,
        medico: row.dataset.medico,
        data_hora: row.dataset.dataHora,
        phone: row.dataset.phone,
      }),
    });

    if (!resp.ok) throw new Error(await resp.text());

    row.remove();
    const contagem = document.getElementById('contagem');
    contagem.textContent = parseInt(contagem.textContent, 10) - 1;
  } catch (err) {
    console.error(err);
    alert('Erro ao registrar pagamento. Tente novamente.');
    btn.disabled = false;
    btn.textContent = 'Pago';
  }
}
</script>
{% endblock %}
```

---

### Task 6: Rodar os testes e commitar

**Files:**
- Test: `tests/test_dashboard_pagamentos.py`

- [ ] **Passo 1: Rodar os novos testes**

```bash
cd /Users/ayexatavares/psique-ai
uv run pytest tests/test_dashboard_pagamentos.py -v
```

Esperado: todos passando.

- [ ] **Passo 2: Rodar a suite completa para checar regressões**

```bash
uv run pytest --tb=short
```

Esperado: todos passando.

- [ ] **Passo 3: Commit**

```bash
git add dashboard/pyproject.toml dashboard/main.py dashboard/templates/pagamentos.html tests/test_dashboard_pagamentos.py
git commit -m "feat: página /pagamentos com tabela de pendências, planilha e e-mail"
```
