# Pagamentos Pendentes embutido no Chatwoot (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar uma seção "Pagamentos" ao painel da atendente já embutido no Chatwoot (`dashboard/templates/atendente.html`), listando e permitindo registrar as pendências (taxa de reserva / consulta) do(s) paciente(s) da conversa aberta, com confirmação automática por WhatsApp ao paciente.

**Architecture:** Extrai a lógica de pendências/pagamento de `dashboard/main.py` (rota `/pagamentos`) para um módulo compartilhado `dashboard/payments.py`, reutilizado tanto pela página cheia (Basic Auth, inalterada) quanto por duas rotas novas em `dashboard/attendant_routes.py` (auth por `ATTENDANT_PANEL_TOKEN`, já existente), filtradas pelos pacientes vinculados ao telefone da conversa. Um módulo novo e pequeno `dashboard/chatwoot_client.py` manda a mensagem de confirmação usando o `conversation_id` que o Chatwoot já entrega no mesmo evento `postMessage` que informa o telefone.

**Tech Stack:** FastAPI, Supabase (postgrest async), httpx, Tailwind (CDN, no template), pytest + pytest-asyncio, Starlette TestClient.

**Pré-requisito de leitura:**
- `docs/superpowers/specs/2026-07-03-pagamentos-embed-chatwoot-design.md` (spec desta entrega)
- `docs/superpowers/specs/2026-06-30-painel-atendente-chatwoot-design.md` (spec do painel base)
- `dashboard/main.py` (rota `/pagamentos` atual — vai ser extraída)
- `dashboard/attendant_routes.py` e `dashboard/attendant_db.py` (padrões de auth por token e resolução de paciente por telefone)
- `dashboard/templates/atendente.html` (UI existente do painel)

**Contexto importante — sincronizar o branch primeiro:** este trabalho continua no branch `feat/painel-atendente` (worktree `.worktrees/painel-atendente`), que foi criado **antes** do refactor de `users` → `patients`/`contacts`/`patient_contacts` ter sido concluído em `main`. A rota `/pagamentos` no branch ainda usa o modelo antigo (`users(...)`). A Task 1 faz merge de `main` para trazer as correções (`4ec6ea7`, `d9f08ac`, `1ebf3d0`) antes de qualquer código novo — sem isso, `payments.py` seria extraído a partir de uma query já quebrada em produção.

**Referência Chatwoot Dashboard Apps (herdada do plano do painel base):** o iframe manda `chatwoot-dashboard-app:fetch-info`; o Chatwoot responde via `message` com `event.data` sendo uma string JSON `{ event: "appContext", data: { conversation, contact, currentAgent } }`. **A verificar no teste manual (Task 8):** se `data.conversation.id` realmente vem populado nesse evento (é a suposição central desta entrega para a confirmação por WhatsApp — ver "Riscos" na spec).

---

## Estrutura de arquivos

- **Criar** `dashboard/payments.py` — `compute_pendencias()`, `mark_paid()` e os helpers de e-mail/planilha, extraídos de `main.py`.
- **Criar** `dashboard/chatwoot_client.py` — `send_confirmation_message()`, POST direto na API do Chatwoot.
- **Modificar** `dashboard/main.py` — remove o código movido; `/pagamentos` e `/api/pagamentos/{id}/pagar` passam a chamar `payments.*`.
- **Modificar** `dashboard/attendant_routes.py` — duas rotas novas: `GET /pagamentos`, `POST /pagamentos/{id}/pagar`.
- **Modificar** `dashboard/templates/atendente.html` — captura `conversation.id`; seção "Pagamentos" (lista + marcar pago).
- **Modificar** `dashboard/tests/conftest.py` — `FakeQuery` ganha suporte a `.in_()`.
- **Criar** `dashboard/tests/test_payments.py`.
- **Modificar** `dashboard/tests/test_attendant_routes.py` — testes das duas rotas novas.
- **Modificar** `tests/test_dashboard_pagamentos.py` (raiz do repo) — só o alvo do `patch(...)` muda (de `main.` para `payments.`), comportamento coberto continua o mesmo.
- **Modificar** `.env.example` — comentário indicando que `CHATWOOT_BASE_URL`/`CHATWOOT_ACCOUNT_ID`/`CHATWOOT_AGENT_BOT_TOKEN` agora também são lidos pelo `dashboard/`.

Rodar testes do dashboard (a partir de `dashboard/`): `uv run pytest -q`
Rodar teste de não-regressão da raiz (a partir da raiz do worktree): `uv run pytest tests/test_dashboard_pagamentos.py --tb=short`

---

## Task 1: Sincronizar o branch com `main`

**Files:** nenhum arquivo específico — operação de git no worktree `.worktrees/painel-atendente`.

- [ ] **Step 1: Conferir o estado do worktree**

```bash
cd /Users/ayexatavares/psique-ai/.worktrees/painel-atendente
git status --short
```

Esperado: só pode aparecer `M dashboard/uv.lock` (drift local de lockfile). Se houver qualquer outra alteração não commitada, pare e avise — não descarte sem entender o que é.

- [ ] **Step 2: Descartar o drift do lockfile (se for só isso)**

```bash
git checkout -- dashboard/uv.lock
git status --short
```

Esperado: working tree limpo.

- [ ] **Step 3: Merge de `main`**

```bash
git fetch origin main 2>/dev/null || true
git merge main -m "merge: trazer refactor patients/contacts e demais fixes de main"
```

Esperado: merge automático sem conflitos (já validado com `git merge-tree` antes de escrever este plano). Se aparecer `CONFLICT`, pare e resolva manualmente olhando o diff — não use `git checkout --ours/--theirs` sem entender a origem da divergência.

- [ ] **Step 4: Instalar dependências e rodar a suíte do dashboard**

```bash
cd dashboard
uv sync
uv run pytest -q
```

Esperado: todos os testes existentes passam (baseline verde antes de mexer em código novo).

- [ ] **Step 5: Rodar o teste de não-regressão da raiz**

```bash
cd /Users/ayexatavares/psique-ai/.worktrees/painel-atendente
uv run pytest tests/test_dashboard_pagamentos.py --tb=short
```

Esperado: 5 testes passando (comportamento atual de `/pagamentos`, já com o modelo `patients`/`contacts` pós-merge).

---

## Task 2: `FakeQuery` ganha suporte a `.in_()`

**Files:**
- Modify: `dashboard/tests/conftest.py`

- [ ] **Step 1: Editar `FakeQuery` para suportar filtros `.in_()` além de `.eq()`**

Trocar o corpo da classe `FakeQuery` (mantém tudo igual, só a representação dos filtros e os métodos `eq`/`_matches`, mais o novo `in_`):

```python
class FakeQuery:
    """Imita o query-builder do postgrest-py para os usos do painel."""
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters = []  # list[tuple[kind, col, val]]

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, values):
        self._filters.append(("in", col, values))
        return self

    def _matches(self, row):
        for kind, col, val in self._filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "in" and row.get(col) not in val:
                return False
        return True

    async def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "select":
            return FakeResult([r for r in rows if self._matches(r)])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows.append(dict(p))
            return FakeResult([dict(p) for p in payload])
        if self._op == "update":
            changed = []
            for r in rows:
                if self._matches(r):
                    r.update(self._payload)
                    changed.append(dict(r))
            return FakeResult(changed)
        if self._op == "delete":
            kept, removed = [], []
            for r in rows:
                (removed if self._matches(r) else kept).append(r)
            self._store[self._table] = kept
            return FakeResult(removed)
        return FakeResult([])
```

- [ ] **Step 2: Rodar a suíte do dashboard para confirmar que nada quebrou**

```bash
cd dashboard
uv run pytest -q
```

Esperado: mesmos testes de antes continuam passando (mudança é aditiva/retrocompatível).

- [ ] **Step 3: Commit**

```bash
git add dashboard/tests/conftest.py
git commit -m "test(dashboard): FakeQuery ganha suporte a .in_()"
```

---

## Task 3: `dashboard/payments.py` — extrair `compute_pendencias` e `mark_paid`

**Files:**
- Create: `dashboard/payments.py`
- Test: `dashboard/tests/test_payments.py`

- [ ] **Step 1: Escrever os testes (falhando, `payments.py` ainda não existe)**

Criar `dashboard/tests/test_payments.py`:

```python
from unittest.mock import AsyncMock

import payments


def _appt(appointment_id, patient_id, patient_name, phone, **overrides):
    row = {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "start_time": "2026-07-10T14:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "paid_at": None,
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
        "consultation_type": None,
        "status": "scheduled",
        "patients": {
            "name": patient_name,
            "birth_date": "1990-01-01",
            "custom_price": None,
            "patient_contacts": [
                {"is_self": True, "contacts": {"phone": phone, "name": patient_name}},
            ],
        },
    }
    row.update(overrides)
    return row


# ── compute_pendencias ────────────────────────────────────────────────────────


async def test_compute_pendencias_sem_filtro_retorna_tudo(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000"),
        _appt("a2", "p2", "Maria", "5581999991111"),
    ]
    out = await payments.compute_pendencias(fake_client)
    assert {p["appointment_id"] for p in out} == {"a1", "a2"}
    # cada agendamento sem taxa nem consulta paga gera 2 pendências
    assert len(out) == 4


async def test_compute_pendencias_filtra_por_patient_ids(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000"),
        _appt("a2", "p2", "Maria", "5581999991111"),
    ]
    out = await payments.compute_pendencias(fake_client, patient_ids=["p1"])
    assert {p["appointment_id"] for p in out} == {"a1"}
    assert all(p["paciente"] == "João" for p in out)


async def test_compute_pendencias_patient_ids_vazio_nao_consulta(fake_client):
    fake_client.store["appointments"] = [_appt("a1", "p1", "João", "5581999990000")]
    out = await payments.compute_pendencias(fake_client, patient_ids=[])
    assert out == []


async def test_compute_pendencias_taxa_ja_paga_nao_aparece(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000",
              booking_fee_paid_at="2026-07-01T00:00:00+00:00"),
    ]
    out = await payments.compute_pendencias(fake_client)
    assert {p["tipo"] for p in out} == {"consulta"}


async def test_compute_pendencias_extrai_telefone_do_contato_self(fake_client):
    fake_client.store["appointments"] = [_appt("a1", "p1", "João", "5581999990000")]
    out = await payments.compute_pendencias(fake_client)
    assert all(p["phone"] == "5581999990000" for p in out)


# ── mark_paid ───────────────────────────────────────────────────────────────


async def test_mark_paid_taxa_atualiza_booking_fee(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "taxa", 100, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["booking_fee_paid_at"] is not None
    assert "paid_at" not in row


async def test_mark_paid_consulta_atualiza_paid_at(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["paid_at"] is not None


async def test_mark_paid_sheet_failure_nao_propaga(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(
        payments, "_append_payment_sheet",
        AsyncMock(side_effect=RuntimeError("sheets down")),
    )
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["paid_at"] is not None  # gravação principal não foi afetada pela falha da planilha
```

- [ ] **Step 2: Rodar e confirmar que falha (módulo `payments` não existe)**

```bash
cd dashboard
uv run pytest tests/test_payments.py -v
```

Esperado: `ModuleNotFoundError: No module named 'payments'`.

- [ ] **Step 3: Criar `dashboard/payments.py`**

```python
"""Lógica de pagamentos pendentes, compartilhada entre a página cheia
(/pagamentos, Basic Auth) e o painel da atendente embutido no Chatwoot
(token, filtrado por paciente).
"""
import asyncio
import logging
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Recife")
_PAYMENTS_SHEET_RANGE = "Pagamentos!A:J"

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
    """Retorna o valor sugerido da consulta (com desconto de R$50 para dinheiro/PIX)."""
    if custom_price is not None:
        return custom_price
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


async def _send_clinic_email(subject: str, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("CLINIC_NOTIFY_EMAIL")
    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    def _send() -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send)


async def _append_payment_sheet(
    patient_name: str,
    phone: str,
    doctor_name: str,
    appointment_dt: str,
    amount: str,
    payment_type: str,
    payment_method: str,
) -> None:
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    if not spreadsheet_id:
        return

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    now = datetime.now(_TZ).strftime("%d/%m/%Y %H:%M")
    row = [now, patient_name, doctor_name, appointment_dt, amount, phone, payment_type, payment_method, "", ""]

    def _write() -> None:
        service = build("sheets", "v4", credentials=creds)
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=_PAYMENTS_SHEET_RANGE,
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write)


async def compute_pendencias(client, patient_ids: list[str] | None = None) -> list[dict]:
    """Retorna a lista de pendências (taxa/consulta) em aberto.

    Sem `patient_ids`: todas as pendências da clínica (usado por /pagamentos).
    Com `patient_ids`: só as pendências desses pacientes (usado pelo painel da atendente).
    Lista vazia em `patient_ids` retorna `[]` sem consultar o banco.
    """
    if patient_ids is not None and not patient_ids:
        return []

    query = (
        client.from_("appointments")
        .select(
            "appointment_id, start_time, doctor_id, paid_at, "
            "booking_fee_paid_at, booking_fee_waived, consultation_type, status, "
            "patients(name, birth_date, custom_price, "
            "patient_contacts(is_self, contacts(phone, name)))"
        )
        .in_("status", ["scheduled", "completed"])
    )
    if patient_ids is not None:
        query = query.in_("patient_id", patient_ids)
    result = await query.execute()

    pendencias = []
    for appt in result.data or []:
        patient = appt.get("patients") or {}
        patient_name = patient.get("name") or "Paciente"
        birth_date = patient.get("birth_date")
        custom_price = patient.get("custom_price")

        # Busca telefone via patient_contacts → contacts
        phone = ""
        patient_contacts = patient.get("patient_contacts") or []
        self_contact = next((pc for pc in patient_contacts if pc.get("is_self")), None)
        pc_row = self_contact or (patient_contacts[0] if patient_contacts else None)
        if pc_row:
            contact = pc_row.get("contacts") or {}
            phone = contact.get("phone") or ""

        doctor_display = DOCTOR_DISPLAY.get(appt.get("doctor_id", ""), "Médico")
        start_time = appt.get("start_time", "")
        try:
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
                "start_time": start_time,
                "tipo": "taxa",
                "tipo_label": "Taxa de reserva",
                "valor": 100,
            })

        if not appt.get("paid_at"):
            valor = _calc_valor_consulta(
                appt.get("doctor_id", ""),
                birth_date,
                appt.get("consultation_type"),
                custom_price,
            )
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "start_time": start_time,
                "tipo": "consulta",
                "tipo_label": "Consulta",
                "valor": valor,
            })

    pendencias.sort(key=lambda x: x["start_time"])
    return pendencias


async def mark_paid(
    client,
    appointment_id: str,
    tipo: str,
    valor: int,
    forma_pagamento: str,
    paciente: str,
    medico: str,
    data_hora: str,
    phone: str,
) -> None:
    """Grava o pagamento no agendamento e tenta registrar na planilha/e-mail (best-effort).

    Assume que `tipo` já foi validado pelo chamador ("taxa" ou "consulta").
    """
    now = datetime.now(timezone.utc).isoformat()

    if tipo == "taxa":
        await client.from_("appointments").update({"booking_fee_paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "taxa_reserva"
    else:
        await client.from_("appointments").update({"paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "consulta"

    forma_label = FORMA_PAGAMENTO_LABEL.get(forma_pagamento, forma_pagamento)
    amount_str = str(valor)

    try:
        await _append_payment_sheet(
            patient_name=paciente,
            phone=phone,
            doctor_name=medico,
            appointment_dt=data_hora,
            amount=amount_str,
            payment_type=payment_type,
            payment_method=forma_pagamento,
        )
    except Exception:
        logger.exception("SHEETS_APPEND FAILED patient=%s", paciente)

    try:
        tipo_label = "Taxa de reserva" if tipo == "taxa" else "Consulta"
        await _send_clinic_email(
            subject=f"Pagamento registrado — {paciente}",
            body=(
                f"💰 Pagamento registrado pelo dashboard\n"
                f"Paciente: {paciente}\n"
                f"Médico: {medico}\n"
                f"Consulta: {data_hora}\n"
                f"Tipo: {tipo_label}\n"
                f"Valor: R$ {amount_str}\n"
                f"Forma: {forma_label}"
            ),
        )
    except Exception:
        logger.exception("EMAIL_FAILED patient=%s", paciente)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

```bash
cd dashboard
uv run pytest tests/test_payments.py -v
```

Esperado: todos os testes de `test_payments.py` PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/payments.py dashboard/tests/test_payments.py
git commit -m "feat(dashboard): extrai payments.py (compute_pendencias, mark_paid) de main.py"
```

---

## Task 4: `dashboard/main.py` passa a delegar para `payments.py`

**Files:**
- Modify: `dashboard/main.py`
- Modify: `tests/test_dashboard_pagamentos.py` (raiz do repo)

- [ ] **Step 1: Remover de `main.py` o código que virou `payments.py`**

Remover de `dashboard/main.py`: o bloco `_TZ` / `_PAYMENTS_SHEET_RANGE`, `_send_clinic_email`, `_append_payment_sheet`, a seção `# ── Pagamentos ──` inteira (`DOCTOR_DISPLAY`, `DOCTOR_KEY`, `FORMA_PAGAMENTO_LABEL`, `_calc_valor_consulta`).

- [ ] **Step 2: Trocar os imports do topo do arquivo**

De:
```python
import asyncio
import logging
import os
import smtplib
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from secrets import compare_digest
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel
from supabase import AsyncClient, acreate_client
```

Para:
```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from secrets import compare_digest

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from supabase import AsyncClient, acreate_client
```

- [ ] **Step 3: Importar `payments` junto com `attendant_routes`**

Trocar:
```python
import attendant_routes

app.include_router(attendant_routes.router)
```

Por:
```python
import attendant_routes
import payments

app.include_router(attendant_routes.router)
```

- [ ] **Step 4: Simplificar a rota `/pagamentos`**

Trocar o corpo inteiro da função `pagamentos_page` (a query + o loop de `pendencias`) por:

```python
@app.get("/pagamentos")
async def pagamentos_page(request: Request, username: str = Depends(verify_credentials)):
    client = get_supabase()
    pendencias = await payments.compute_pendencias(client)
    return templates.TemplateResponse(
        request, "pagamentos.html", {"username": username, "pendencias": pendencias}
    )
```

- [ ] **Step 5: Simplificar `api_pagar`**

Trocar o corpo de `api_pagar` por:

```python
@app.post("/api/pagamentos/{appointment_id}/pagar")
async def api_pagar(
    appointment_id: str,
    body: PagarBody,
    username: str = Depends(verify_credentials),
):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    client = get_supabase()
    await payments.mark_paid(
        client, appointment_id, body.tipo, body.valor, body.forma_pagamento,
        body.paciente, body.medico, body.data_hora, body.phone,
    )
    return {"ok": True}
```

(A classe `PagarBody` continua igual, não mexer.)

- [ ] **Step 6: Ajustar `tests/test_dashboard_pagamentos.py` (raiz) — patch aponta para `payments`, não `main`**

Em `tests/test_dashboard_pagamentos.py`, trocar as duas ocorrências:

```python
with patch("main._append_payment_sheet", new_callable=AsyncMock) as mock_sheets, \
     patch("main._send_clinic_email", new_callable=AsyncMock) as mock_email:
```

Por (nas duas funções de teste que usam esse padrão, `test_pagar_taxa_atualiza_booking_fee` e `test_pagar_consulta_atualiza_paid_at`):

```python
with patch("payments._append_payment_sheet", new_callable=AsyncMock) as mock_sheets, \
     patch("payments._send_clinic_email", new_callable=AsyncMock) as mock_email:
```

- [ ] **Step 7: Rodar a suíte da raiz e confirmar que passa sem mais mudanças**

```bash
cd /Users/ayexatavares/psique-ai/.worktrees/painel-atendente
uv run pytest tests/test_dashboard_pagamentos.py --tb=short -v
```

Esperado: 5 testes PASS (mesmo comportamento de antes, só o alvo do patch mudou).

- [ ] **Step 8: Rodar a suíte do dashboard também (garantir que a Task 0/1 continuam verdes)**

```bash
cd dashboard
uv run pytest -q
```

Esperado: todos PASS.

- [ ] **Step 9: Commit**

```bash
cd /Users/ayexatavares/psique-ai/.worktrees/painel-atendente
git add dashboard/main.py tests/test_dashboard_pagamentos.py
git commit -m "refactor(dashboard): /pagamentos e api_pagar delegam para payments.py"
```

---

## Task 5: `dashboard/chatwoot_client.py` — enviar confirmação por WhatsApp

**Files:**
- Create: `dashboard/chatwoot_client.py`
- Test: `dashboard/tests/test_chatwoot_client.py`

- [ ] **Step 1: Escrever o teste (falhando, módulo ainda não existe)**

Criar `dashboard/tests/test_chatwoot_client.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import chatwoot_client


async def test_send_confirmation_message_posts_to_chatwoot(monkeypatch):
    monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chatwoot.example.com")
    monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.setenv("CHATWOOT_AGENT_BOT_TOKEN", "bot-token-123")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = mock_post

    with patch("chatwoot_client.httpx.AsyncClient", return_value=mock_client):
        await chatwoot_client.send_confirmation_message(42, "Recebemos seu pagamento!")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://chatwoot.example.com/api/v1/accounts/1/conversations/42/messages"
    assert kwargs["json"] == {"content": "Recebemos seu pagamento!", "message_type": "outgoing"}
    assert kwargs["headers"]["api_access_token"] == "bot-token-123"
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
cd dashboard
uv run pytest tests/test_chatwoot_client.py -v
```

Esperado: `ModuleNotFoundError: No module named 'chatwoot_client'`.

- [ ] **Step 3: Criar `dashboard/chatwoot_client.py`**

```python
"""Cliente mínimo da API do Chatwoot para o painel da atendente.

Autocontido (não importa app/) — só o necessário para mandar uma mensagem
de confirmação na conversa já aberta no iframe. Usa o `conversation_id` que
o próprio Chatwoot entrega no evento postMessage, então não precisa
replicar a busca/criação de contato e conversa que existe em app/chatwoot.py.
"""
import os

import httpx


async def send_confirmation_message(conversation_id: int, text: str) -> None:
    base_url = os.environ["CHATWOOT_BASE_URL"].rstrip("/")
    account_id = os.environ["CHATWOOT_ACCOUNT_ID"]
    url = f"{base_url}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    headers = {
        "api_access_token": os.environ["CHATWOOT_AGENT_BOT_TOKEN"],
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url, json={"content": text, "message_type": "outgoing"}, headers=headers,
        )
        response.raise_for_status()
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

```bash
cd dashboard
uv run pytest tests/test_chatwoot_client.py -v
```

Esperado: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/chatwoot_client.py dashboard/tests/test_chatwoot_client.py
git commit -m "feat(dashboard): chatwoot_client.send_confirmation_message"
```

---

## Task 6: Rotas novas em `attendant_routes.py`

**Files:**
- Modify: `dashboard/attendant_routes.py`
- Modify: `dashboard/tests/test_attendant_routes.py`

- [ ] **Step 1: Escrever os testes (falhando, rotas ainda não existem)**

Adicionar ao final de `dashboard/tests/test_attendant_routes.py`:

```python
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
                              paciente, medico, data_hora, phone):
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
cd dashboard
uv run pytest tests/test_attendant_routes.py -v
```

Esperado: falhas em `AttributeError`/404 nos testes novos (rotas e `get_client` ainda não existem em `attendant_routes`).

- [ ] **Step 3: Adicionar as rotas em `dashboard/attendant_routes.py`**

No topo do arquivo, trocar:

```python
import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

import attendant_db

router = APIRouter(prefix="/api/atendente")
```

Por:

```python
import logging
import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

import attendant_db
import chatwoot_client
import payments
from db_client import get_client

router = APIRouter(prefix="/api/atendente")
logger = logging.getLogger(__name__)
```

Ao final do arquivo (depois de `reset_checkpoint`), adicionar:

```python
# ── Pagamentos ────────────────────────────────────────────────────────────────


class AtendentePagarBody(BaseModel):
    tipo: str             # "taxa" ou "consulta"
    valor: int
    forma_pagamento: str  # "PIX", "cartao_credito", "cartao_debito", "dinheiro"
    paciente: str
    medico: str
    data_hora: str
    phone: str
    conversation_id: int | None = None


_CONFIRM_TEXT = {
    "taxa": (
        "Olá, {paciente}! 👋 Recebemos o pagamento da taxa de reserva da sua consulta "
        "com {medico}. Sua vaga está garantida! ✅"
    ),
    "consulta": (
        "Olá, {paciente}! 👋 Recebemos o pagamento da sua consulta com {medico}. Obrigado! ✅"
    ),
}


@router.get("/pagamentos")
async def pagamentos(phone: str, _: None = Depends(verify_token)):
    resolved = await attendant_db.resolve_contact_and_patients(phone)
    patient_ids = [p["id"] for p in resolved["patients"]]
    client = await get_client()
    return await payments.compute_pendencias(client, patient_ids=patient_ids)


@router.post("/pagamentos/{appointment_id}/pagar")
async def pagar(appointment_id: str, body: AtendentePagarBody, _: None = Depends(verify_token)):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    client = await get_client()
    await payments.mark_paid(
        client, appointment_id, body.tipo, body.valor, body.forma_pagamento,
        body.paciente, body.medico, body.data_hora, body.phone,
    )

    if body.conversation_id is not None:
        try:
            text = _CONFIRM_TEXT[body.tipo].format(paciente=body.paciente, medico=body.medico)
            await chatwoot_client.send_confirmation_message(body.conversation_id, text)
        except Exception:
            logger.exception("CONFIRM_MSG_FAILED appt=%s conversation_id=%s",
                             appointment_id, body.conversation_id)

    await attendant_db.log_event("attendant_pagamento_registrado", body.phone, {
        "appointment_id": appointment_id, "tipo": body.tipo, "valor": body.valor,
    })
    return {"ok": True}
```

- [ ] **Step 4: Rodar e confirmar que os testes passam**

```bash
cd dashboard
uv run pytest tests/test_attendant_routes.py -v
```

Esperado: todos PASS, incluindo os testes antigos (não devem ter sido afetados).

- [ ] **Step 5: Rodar a suíte inteira do dashboard**

```bash
uv run pytest -q
```

Esperado: tudo PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py
git commit -m "feat(dashboard): rotas GET/POST /api/atendente/pagamentos com confirmação por WhatsApp"
```

---

## Task 7: UI — seção "Pagamentos" em `atendente.html`

**Files:**
- Modify: `dashboard/templates/atendente.html`

Sem teste automatizado dedicado (é HTML/JS renderizado; a cobertura de comportamento já está nas rotas da Task 6). A verificação é manual, na Task 8.

- [ ] **Step 1: Capturar `conversation.id` no `postMessage`**

Trocar:

```html
<script>
const TOKEN = {{ token | tojson }};
let PHONE = null;
let CONTACT = null;

// ── 1. Obter telefone: Chatwoot postMessage ou ?phone= (teste) ──────────────
function initPhone() {
  const qp = new URLSearchParams(location.search).get("phone");
  if (qp) { PHONE = qp; load(); return; }
  window.addEventListener("message", (e) => {
    try {
      const data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      if (data && data.event === "appContext" && data.data && data.data.contact) {
        PHONE = data.data.contact.phone_number || PHONE;
        if (PHONE) load();
      }
    } catch (_) {}
  });
  window.parent.postMessage("chatwoot-dashboard-app:fetch-info", "*");
}
```

Por:

```html
<script>
const TOKEN = {{ token | tojson }};
let PHONE = null;
let CONTACT = null;
let CONVERSATION_ID = null;

// ── 1. Obter telefone: Chatwoot postMessage ou ?phone= (teste) ──────────────
function initPhone() {
  const qp = new URLSearchParams(location.search).get("phone");
  if (qp) { PHONE = qp; load(); return; }
  window.addEventListener("message", (e) => {
    try {
      const data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      if (data && data.event === "appContext" && data.data && data.data.contact) {
        PHONE = data.data.contact.phone_number || PHONE;
        if (data.data.conversation && data.data.conversation.id) {
          CONVERSATION_ID = data.data.conversation.id;
        }
        if (PHONE) load();
      }
    } catch (_) {}
  });
  window.parent.postMessage("chatwoot-dashboard-app:fetch-info", "*");
}
```

- [ ] **Step 2: Adicionar o container HTML da seção de pagamentos**

Trocar:

```html
    <div id="forms" class="hidden space-y-4"></div>

    <div id="reset-box" class="hidden mt-6 border-t pt-4">
```

Por:

```html
    <div id="pagamentos-box" class="hidden mb-4">
      <h2 class="font-medium text-gray-700 mb-2">Pagamentos pendentes</h2>
      <div id="pagamentos-list" class="space-y-2"></div>
    </div>

    <div id="forms" class="hidden space-y-4"></div>

    <div id="reset-box" class="hidden mt-6 border-t pt-4">
```

- [ ] **Step 3: Chamar `loadPagamentos()` a partir de `load()`**

Trocar:

```javascript
  setStatus(`Contato: ${contact.name || contact.phone}`);
  document.getElementById("reset-box").classList.remove("hidden");

  const sel = document.getElementById("patient-select");
```

Por:

```javascript
  setStatus(`Contato: ${contact.name || contact.phone}`);
  document.getElementById("reset-box").classList.remove("hidden");
  loadPagamentos();

  const sel = document.getElementById("patient-select");
```

- [ ] **Step 4: Adicionar as funções de pagamentos**

Logo antes de `// ── 3. Carregar paciente + vínculo ──`, adicionar:

```javascript
// ── 2b. Pagamentos pendentes ─────────────────────────────────────────────────
async function loadPagamentos() {
  const box = document.getElementById("pagamentos-box");
  const list = document.getElementById("pagamentos-list");
  const r = await fetch(`/api/atendente/pagamentos?phone=${encodeURIComponent(PHONE)}&token=${encodeURIComponent(TOKEN)}`);
  if (!r.ok) return;
  const pendencias = await r.json();
  box.classList.remove("hidden");
  if (!pendencias.length) {
    list.innerHTML = `<p class="text-sm text-gray-400">Nenhum pagamento pendente.</p>`;
    return;
  }
  list.innerHTML = pendencias.map(pagamentoCard).join("");
}

function pagamentoCard(p) {
  const tipoLabel = p.tipo === "taxa" ? "Taxa de reserva" : "Consulta";
  const badgeClass = p.tipo === "taxa" ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700";
  return `<div class="bg-white rounded-lg shadow p-3" data-appt="${p.appointment_id}" data-tipo="${p.tipo}"
       data-paciente="${p.paciente}" data-medico="${p.medico}" data-data-hora="${p.data_hora}" data-phone="${p.phone}">
    <div class="flex justify-between items-start mb-1">
      <span class="text-sm font-medium text-gray-700">${p.paciente}</span>
      <span class="text-xs px-2 py-0.5 rounded-full ${badgeClass}">${tipoLabel}</span>
    </div>
    <div class="text-xs text-gray-500 mb-2">${p.medico} · ${p.data_hora}</div>
    <div class="flex items-center gap-2">
      <input type="number" min="0" value="${p.valor}" class="valor-input w-20 border border-gray-300 rounded px-2 py-1 text-sm">
      <select class="forma-input border border-gray-300 rounded px-2 py-1 text-sm">
        <option value="PIX">PIX</option>
        <option value="cartao_credito">Cartão de crédito</option>
        <option value="cartao_debito">Cartão de débito</option>
        <option value="dinheiro">Dinheiro</option>
      </select>
      <button onclick="marcarPago(this)" class="bg-wa-green hover:bg-wa-green-dk text-white text-xs px-3 py-1.5 rounded ml-auto">Marcar pago</button>
    </div>
  </div>`;
}

async function marcarPago(btn) {
  const card = btn.closest("[data-appt]");
  const valor = parseInt(card.querySelector(".valor-input").value, 10);
  const forma_pagamento = card.querySelector(".forma-input").value;
  if (isNaN(valor) || valor < 0) { alert("Valor inválido."); return; }
  btn.disabled = true;
  const r = await fetch(`/api/atendente/pagamentos/${card.dataset.appt}/pagar?token=${encodeURIComponent(TOKEN)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tipo: card.dataset.tipo, valor, forma_pagamento,
      paciente: card.dataset.paciente, medico: card.dataset.medico,
      data_hora: card.dataset.dataHora, phone: card.dataset.phone,
      conversation_id: CONVERSATION_ID,
    }),
  });
  if (!r.ok) { alert("Erro ao registrar pagamento."); btn.disabled = false; return; }
  card.remove();
}

```

- [ ] **Step 5: Rodar a suíte do dashboard inteira (garante que a rota `/atendente` ainda renderiza)**

```bash
cd dashboard
uv run pytest -q
```

Esperado: tudo PASS, incluindo `test_atendente_page_renders`.

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(dashboard): seção de pagamentos pendentes no painel da atendente"
```

---

## Task 8: `.env.example` — documentar variáveis do Chatwoot usadas pelo dashboard

**Files:**
- Modify: `.env.example` (raiz do repo)

- [ ] **Step 1: Adicionar um comentário junto ao bloco do Chatwoot**

Trocar:

```
# Chatwoot — Agent Bot inbox
CHATWOOT_BASE_URL=https://evolution-chatwoot.5pqooc.easypanel.host
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_AGENT_BOT_TOKEN=your_agent_bot_token_here
CHATWOOT_USER_TOKEN=your_user_api_token_here  # token de agente humano (perfil → Access Token no Chatwoot)
CHATWOOT_INBOX_ID=1
```

Por:

```
# Chatwoot — Agent Bot inbox
# CHATWOOT_BASE_URL / CHATWOOT_ACCOUNT_ID / CHATWOOT_AGENT_BOT_TOKEN também são lidos
# pelo dashboard/ (painel da atendente) para mandar a confirmação de pagamento — configurar
# essas 3 no ambiente de deploy do dashboard também, não só no do bot.
CHATWOOT_BASE_URL=https://evolution-chatwoot.5pqooc.easypanel.host
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_AGENT_BOT_TOKEN=your_agent_bot_token_here
CHATWOOT_USER_TOKEN=your_user_api_token_here  # token de agente humano (perfil → Access Token no Chatwoot)
CHATWOOT_INBOX_ID=1
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: documenta uso de CHATWOOT_* pelo dashboard/ (confirmação de pagamento)"
```

---

## Task 9: Verificação final

**Files:** nenhum (só rodar suítes e checklist manual).

- [ ] **Step 1: Suíte completa do dashboard**

```bash
cd dashboard
uv run pytest -q
```

Esperado: todos PASS.

- [ ] **Step 2: Suíte de não-regressão da raiz**

```bash
cd /Users/ayexatavares/psique-ai/.worktrees/painel-atendente
uv run pytest tests/test_dashboard_pagamentos.py --tb=short
```

Esperado: todos PASS.

- [ ] **Step 3: Checklist manual pendente para produção (não faz parte deste plano, documentar para o usuário)**

Depois de mergear/deployar:
1. Configurar em produção do `dashboard/`: `ATTENDANT_PANEL_TOKEN`, `CHATWOOT_FRAME_ANCESTOR`, `CHATWOOT_BASE_URL`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_AGENT_BOT_TOKEN` (as 3 últimas hoje só existem no ambiente do bot).
2. No Chatwoot: Configurações → Integrações → Dashboard Apps → adicionar `https://<dashboard>/atendente?token=<ATTENDANT_PANEL_TOKEN>`.
3. Abrir uma conversa de teste e confirmar: (a) a seção "Pagamentos" aparece com as pendências certas; (b) "Marcar pago" registra e faz a mensagem de confirmação aparecer na própria conversa do Chatwoot.
4. **Se o passo 3b falhar** (mensagem não chega), o suspeito nº 1 é a suposição não verificada de que `data.conversation.id` vem no evento `appContext` — inspecionar o payload real (`console.log` temporário em `initPhone()`) antes de investigar outras causas.

---

## Fora de escopo (confirmado na spec)

- Seletor de paciente na seção de pagamentos.
- Qualquer mudança de comportamento em `/pagamentos` (página cheia) além da extração para `payments.py`.
- Configuração do Dashboard App no próprio Chatwoot (checklist manual da Task 9, não é código).
