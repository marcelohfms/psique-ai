# Desfechos de consulta (Não compareceu e Alta) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o médico (no `/retornos`) e a atendente (no painel de pagamentos) marquem uma consulta como **não compareceu** (`no_show`) ou **alta**, com os efeitos de negócio associados (retenção de taxa, fim dos lembretes de retorno, mensagem de falta, aviso de retenção na remarcação).

**Architecture:** Reaproveita telas existentes do dashboard. `no_show` é um novo valor de `appointments.status`; `alta` é o sentinela `return_interval="alta"` em `return_reminders`. Uma coluna nova `appointments.no_show_message_sent_at` controla o envio da mensagem de falta por um cron diário. O aviso de retenção na remarcação reusa o padrão já existente em `app/graph/tools.py`.

**Tech Stack:** FastAPI (dashboard), Supabase/Postgres, LangGraph + OpenAI (bot), pytest, GitHub Actions.

---

## Contexto do codebase (leia antes de começar)

- **Dashboard** (`dashboard/`) é um serviço FastAPI separado que **não importa `app/`** (imagem Docker própria). Ele fala com Supabase via um cliente compartilhado. Testes usam um fake client em `dashboard/tests/conftest.py` (`FakeClient`/`FakeQuery`) — um store em memória `{tabela: [linhas]}`. Rode os testes do dashboard a partir da pasta `dashboard/` (o `pytest.ini` dela adiciona `.` ao path; por isso os imports nos testes são `import return_reminders as rr`, sem prefixo `dashboard.`).
- **`/retornos`** (`dashboard/main.py:277`): fila onde o médico classifica cada consulta `completed` (`dashboard/return_reminders.py::get_pending_classification`). Hoje a única saída é escolher um intervalo (`save_classification`).
- **Painel de pagamentos** (`dashboard/payments.py::compute_pendencias`, template `dashboard/templates/pagamentos.html`): lista pendências de `taxa`/`consulta` de appointments com `status IN ('scheduled','completed')`. É o mesmo painel embutido no Chatwoot para a atendente.
- **Crons** (`scripts/`) rodam via GitHub Actions e **importam `app/`**. Padrão de referência: `scripts/complete_appointments.py` (busca appointments, envia WhatsApp via `app.chatwoot`, marca flag de envio). Contatos de um paciente: `app.patients.get_contacts_for_patient(patient_id, "consulta")`.
- **Bot** (`app/graph/`): `tools.py:1508-1525` já emite a mensagem "taxa recolhida + nova taxa de R$100" para remarcações fora do prazo. Reaproveitar esse padrão para o `no_show`.

Comandos de teste:
- App/scripts: `uv run pytest --tb=short` (raiz do repo).
- Dashboard: `cd dashboard && uv run pytest --tb=short`.

**Constraint atual importante:** `return_reminders.return_interval` tem `CHECK (... IN ('15_dias','1_mes','2_meses','3_meses','4_meses','6_meses'))` e `next_return_date` é `NOT NULL` (ver `supabase/migrations/20260714_create_return_reminders.sql` + `20260722_...`). `appointments.status` **não** tem CHECK nas migrations do repo (coluna TEXT livre) — `no_show` não exige migration de constraint.

---

## File Structure

- `supabase/migrations/20260811_no_show_and_alta.sql` — **Create**: aceita `alta` no CHECK, torna `next_return_date` anulável, adiciona `no_show_message_sent_at`.
- `dashboard/return_reminders.py` — **Modify**: `save_discharge()` (alta), `mark_no_show()` (falta), exclusão defensiva de `no_show` na fila, constante `ALTA`.
- `dashboard/main.py` — **Modify**: endpoints para alta e no_show (a partir de `/retornos` e do painel de pagamentos).
- `dashboard/templates/retornos.html` — **Modify**: botões "Não compareceu" e "Alta".
- `dashboard/templates/pagamentos.html` — **Modify**: botão "Não compareceu" por pendência.
- `scripts/send_no_show_messages.py` — **Create**: cron da mensagem de falta.
- `scripts/complete_appointments.py` — **Modify**: pular pós-consulta se o paciente teve alta.
- `scripts/send_return_reminders.py` — **Modify**: `pending_template` ignora `return_interval == "alta"`.
- `app/graph/tools.py` + `app/graph/prompts.py` — **Modify**: aviso de retenção quando um paciente `no_show` pede remarcação.
- `.github/workflows/` — **Modify/Create**: agendar o novo cron.
- Testes correspondentes em `dashboard/tests/` e `tests/`.

---

## Task 1: Migration — CHECK do `alta`, `next_return_date` anulável, coluna de flag

**Files:**
- Create: `supabase/migrations/20260811_no_show_and_alta.sql`

- [ ] **Step 1: Escrever a migration**

Segue o padrão da `20260722_add_2_4_meses_return_interval.sql` (descobre o nome real da constraint antes de dropar).

```sql
-- Desfechos de consulta: 'alta' (return_reminders) e mensagem de falta.
-- 1) return_interval passa a aceitar o sentinela 'alta' (paciente recebeu
--    alta: sem próximo retorno). 2) next_return_date deixa de ser NOT NULL
--    (alta não tem data de retorno). 3) appointments ganha flag de envio da
--    mensagem de falta. appointments.status NÃO tem CHECK no repo — 'no_show'
--    não precisa de alteração de constraint.

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'return_reminders'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%return_interval%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE return_reminders DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE return_reminders ADD CONSTRAINT return_reminders_return_interval_check
    CHECK (return_interval IN ('15_dias','1_mes','2_meses','3_meses','4_meses','6_meses','alta'));

ALTER TABLE return_reminders ALTER COLUMN next_return_date DROP NOT NULL;

ALTER TABLE appointments ADD COLUMN IF NOT EXISTS no_show_message_sent_at TIMESTAMPTZ;
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/20260811_no_show_and_alta.sql
git commit -m "feat(db): aceita 'alta' e no_show_message_sent_at (desfechos de consulta)"
```

> Nota de execução: a migration é aplicada no Supabase pelo processo de deploy do projeto (não há runner local de migration neste repo). Os testes usam o fake client em memória e não dependem dela.

---

## Task 2: `save_discharge` (alta) em `return_reminders.py`

**Files:**
- Modify: `dashboard/return_reminders.py`
- Test: `dashboard/tests/test_return_reminders.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione ao fim de `dashboard/tests/test_return_reminders.py`:

```python
# ── save_discharge (alta) ──────────────────────────────────────────────────


async def test_save_discharge_grava_sentinela_alta_sem_next_return_date(fake_client):
    saved = await rr.save_discharge(fake_client, "p1", JULIO_ID, "a1")
    assert saved["return_interval"] == "alta"
    assert saved["next_return_date"] is None
    assert saved["last_classified_appointment_id"] == "a1"
    # gravou de fato 1 linha
    rows = fake_client.store["return_reminders"]
    assert len(rows) == 1
    assert rows[0]["patient_id"] == "p1"


async def test_save_discharge_atualiza_linha_existente(fake_client):
    fake_client.store["return_reminders"] = [{
        "id": "rr1", "patient_id": "p1", "doctor_id": JULIO_ID,
        "return_interval": "1_mes", "next_return_date": "2026-09-13",
        "last_classified_appointment_id": "a0",
    }]
    await rr.save_discharge(fake_client, "p1", JULIO_ID, "a1")
    rows = fake_client.store["return_reminders"]
    assert len(rows) == 1  # upsert, não insere segunda linha
    assert rows[0]["return_interval"] == "alta"
    assert rows[0]["next_return_date"] is None
    assert rows[0]["last_classified_appointment_id"] == "a1"


async def test_save_discharge_tira_paciente_da_fila(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    await rr.save_discharge(fake_client, "p1", JULIO_ID, "a1")
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert out == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -k save_discharge -v`
Expected: FAIL com `AttributeError: module 'return_reminders' has no attribute 'save_discharge'`.

- [ ] **Step 3: Implementar `save_discharge`**

Em `dashboard/return_reminders.py`, adicione a constante perto de `RETURN_INTERVALS` (linha ~13):

```python
ALTA = "alta"  # sentinela em return_interval: paciente recebeu alta, sem próximo retorno
```

E adicione a função logo após `save_classification`:

```python
async def save_discharge(
    client,
    patient_id: str,
    doctor_id: str,
    appointment_id: str,
) -> dict:
    """Registra ALTA do paciente: para os lembretes de retorno sem agendar um novo.

    Grava a mesma linha 1-por-paciente de `save_classification`, mas com o
    sentinela `return_interval="alta"` e `next_return_date=None`. O
    `last_classified_appointment_id` é o que tira a consulta da fila do
    /retornos. Não valida contra RETURN_INTERVALS de propósito — "alta" é um
    desfecho terminal, não um intervalo.
    """
    now = datetime.now(_TZ).isoformat()
    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "return_interval": ALTA,
        "next_return_date": None,
        "last_classified_appointment_id": appointment_id,
        "month_before_sent_at": now,
        "month_of_sent_at": now,
        "overdue_sent_at": now,
        "updated_at": now,
    }
    existing = await (
        client.from_("return_reminders").select("id").eq("patient_id", patient_id).execute()
    )
    if existing.data:
        result = await (
            client.from_("return_reminders").update(payload).eq("patient_id", patient_id).execute()
        )
    else:
        result = await client.from_("return_reminders").insert(payload).execute()
    return (result.data or [payload])[0]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -k save_discharge -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add dashboard/return_reminders.py dashboard/tests/test_return_reminders.py
git commit -m "feat(retornos): save_discharge marca alta (para lembretes de retorno)"
```

---

## Task 3: `mark_no_show` + exclusão defensiva na fila

**Files:**
- Modify: `dashboard/return_reminders.py`
- Test: `dashboard/tests/test_return_reminders.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione ao fim de `dashboard/tests/test_return_reminders.py`:

```python
# ── mark_no_show ───────────────────────────────────────────────────────────


async def test_mark_no_show_muda_status(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed"),
    ]
    await rr.mark_no_show(fake_client, "a1")
    assert fake_client.store["appointments"][0]["status"] == "no_show"


async def test_mark_no_show_tira_da_fila(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    await rr.mark_no_show(fake_client, "a1")
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert out == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -k no_show -v`
Expected: FAIL com `AttributeError: ... 'mark_no_show'`.

- [ ] **Step 3: Implementar `mark_no_show`**

Em `dashboard/return_reminders.py`, adicione:

```python
async def mark_no_show(client, appointment_id: str) -> None:
    """Marca a consulta como falta (`status='no_show'`).

    Registro durável e de primeira classe: distingue quem faltou de quem
    compareceu. Como `get_pending_classification` e `compute_pendencias` só
    olham `completed`/`scheduled`, isso tira a consulta da fila do médico e
    das pendências da atendente. A taxa já paga fica retida (default passivo);
    override é manual, pela atendente, no fluxo de reembolso existente.
    """
    await (
        client.from_("appointments")
        .update({"status": "no_show", "updated_at": datetime.now(_TZ).isoformat()})
        .eq("appointment_id", appointment_id)
        .execute()
    )
```

- [ ] **Step 4: Exclusão defensiva na fila**

`get_pending_classification` já filtra `status='completed'`, então `no_show` fica de fora hoje. Torne isso à prova de futuras mudanças (caso a query passe a incluir consultas passadas ainda `scheduled`): logo após montar `latest_by_patient`, nada a fazer — mas adicione o guard explícito no loop. Substitua o loop de `latest_by_patient` por:

```python
    latest_by_patient: dict[str, dict] = {}
    for appt in appts_result.data or []:
        if appt.get("status") == "no_show":
            continue  # falta nunca entra na fila de classificação
        patient_id = appt.get("patient_id")
        if not patient_id:
            continue
        current = latest_by_patient.get(patient_id)
        if current is None or appt["start_time"] > current["start_time"]:
            latest_by_patient[patient_id] = appt
```

E acrescente `status` ao `.select(...)` de `get_pending_classification`:

```python
        .select("appointment_id, start_time, patient_id, status, patients(name)")
```

- [ ] **Step 5: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: PASS (todos, incluindo os antigos).

- [ ] **Step 6: Commit**

```bash
git add dashboard/return_reminders.py dashboard/tests/test_return_reminders.py
git commit -m "feat(retornos): mark_no_show + exclui falta da fila de classificação"
```

---

## Task 4: Endpoints de alta e no_show em `main.py` (a partir do /retornos)

**Files:**
- Modify: `dashboard/main.py:270-303` (RetornoBody + rotas)
- Test: `dashboard/tests/test_main_retornos.py`

- [ ] **Step 1: Escrever o teste que falha**

Veja o estilo existente em `dashboard/tests/test_main_retornos.py` (usa o `TestClient` do FastAPI com auth básica). Adicione:

```python
def test_api_alta_grava_e_responde_ok(client, auth, seed):
    seed["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "doctor_id": JULIO_ID,
         "status": "completed", "start_time": "2026-07-01T12:00:00+00:00",
         "patients": {"name": "João"}},
    ]
    resp = client.post(
        "/api/retornos/p1/alta",
        json={"doctor_id": JULIO_ID, "appointment_id": "a1"},
        auth=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert seed["return_reminders"][0]["return_interval"] == "alta"


def test_api_no_show_muda_status(client, auth, seed):
    seed["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "doctor_id": JULIO_ID,
         "status": "completed", "start_time": "2026-07-01T12:00:00+00:00",
         "patients": {"name": "João"}},
    ]
    resp = client.post(
        "/api/retornos/p1/no-show",
        json={"appointment_id": "a1"},
        auth=auth,
    )
    assert resp.status_code == 200
    assert seed["appointments"][0]["status"] == "no_show"
```

> Ajuste os fixtures (`client`, `auth`, `seed`, `JULIO_ID`) para casar com os que já existem no topo de `test_main_retornos.py`. Se o arquivo usar outro nome de fixture para o store, use-o.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_main_retornos.py -k "alta or no_show" -v`
Expected: FAIL com 404 (rotas não existem).

- [ ] **Step 3: Implementar as rotas**

Em `dashboard/main.py`, adicione os modelos e rotas perto de `api_salvar_retorno` (linha ~295):

```python
class AltaBody(BaseModel):
    doctor_id: str
    appointment_id: str


class NoShowBody(BaseModel):
    appointment_id: str


@app.post("/api/retornos/{patient_id}/alta")
async def api_alta(patient_id: str, body: AltaBody, username: str = Depends(verify_credentials)):
    client = get_supabase()
    saved = await return_reminders.save_discharge(
        client, patient_id, body.doctor_id, body.appointment_id,
    )
    return {"ok": True, "return_reminder": saved}


@app.post("/api/retornos/{patient_id}/no-show")
async def api_no_show(patient_id: str, body: NoShowBody, username: str = Depends(verify_credentials)):
    client = get_supabase()
    await return_reminders.mark_no_show(client, body.appointment_id)
    return {"ok": True}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_main_retornos.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/main.py dashboard/tests/test_main_retornos.py
git commit -m "feat(retornos): endpoints de alta e no_show"
```

---

## Task 5: Botões "Não compareceu" e "Alta" no `/retornos`

**Files:**
- Modify: `dashboard/templates/retornos.html`

- [ ] **Step 1: Ler o template atual**

Abra `dashboard/templates/retornos.html` e localize o loop dos pendentes (`{% for p in pendentes %}`) e o bloco de botões de intervalo (que faz `POST /api/retornos/{{ p.patient_id }}`). Cada botão de intervalo já monta o body `{doctor_id, appointment_id, appointment_date, return_interval}`.

- [ ] **Step 2: Adicionar os dois botões**

No mesmo grupo de botões de cada pendente, adicione (ajuste as classes CSS ao padrão do arquivo):

```html
<button type="button"
        onclick="marcarNoShow('{{ p.patient_id }}', '{{ p.appointment_id }}', this)">
  Não compareceu
</button>
<button type="button"
        onclick="marcarAlta('{{ p.patient_id }}', '{{ medico_doctor_id }}', '{{ p.appointment_id }}', this)">
  Alta
</button>
```

E, no `<script>` da página, ao lado da função que salva o intervalo, adicione:

```javascript
async function marcarNoShow(patientId, appointmentId, btn) {
  if (!confirm('Marcar como NÃO COMPARECEU? A taxa paga fica retida.')) return;
  btn.disabled = true;
  const r = await fetch(`/api/retornos/${patientId}/no-show`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ appointment_id: appointmentId }),
  });
  if (r.ok) { location.reload(); } else { btn.disabled = false; alert('Falha ao marcar.'); }
}

async function marcarAlta(patientId, doctorId, appointmentId, btn) {
  if (!confirm('Dar ALTA? O paciente para de receber lembretes de retorno.')) return;
  btn.disabled = true;
  const r = await fetch(`/api/retornos/${patientId}/alta`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ doctor_id: doctorId, appointment_id: appointmentId }),
  });
  if (r.ok) { location.reload(); } else { btn.disabled = false; alert('Falha ao dar alta.'); }
}
```

- [ ] **Step 3: Verificação manual (smoke)**

Run: `cd dashboard && uv run pytest tests/test_main_retornos.py -v` (garante que a página `/retornos` ainda renderiza — o teste de GET existente cobre isso).
Expected: PASS. Se houver teste de render da página, confirme que os novos botões aparecem no HTML.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/retornos.html
git commit -m "feat(retornos): botões Não compareceu e Alta na fila"
```

---

## Task 6: `compute_pendencias` exclui `no_show` (teste) + rota no_show do painel de pagamentos

**Files:**
- Modify: `dashboard/main.py`
- Test: `dashboard/tests/test_payments.py` e `dashboard/tests/test_main_payments.py`

- [ ] **Step 1: Teste — no_show não vira pendência**

Em `dashboard/tests/test_payments.py`, adicione (ajuste helper de seed ao padrão do arquivo):

```python
async def test_compute_pendencias_ignora_no_show(fake_client):
    fake_client.store["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "doctor_id": JULIO_ID,
         "status": "no_show", "start_time": "2026-07-01T12:00:00+00:00",
         "end_time": "2026-07-01T13:00:00+00:00",
         "booking_fee_paid_at": None, "booking_fee_waived": False,
         "consultation_type": "acompanhamento", "paid_at": None,
         "patients": {"name": "João"}},
    ]
    out = await payments.compute_pendencias(fake_client)
    assert out == []
```

Run: `cd dashboard && uv run pytest tests/test_payments.py -k no_show -v`
Expected: PASS de imediato (o filtro `status IN ('scheduled','completed')` em `compute_pendencias` já exclui `no_show`). Este teste é de **regressão** — trava o comportamento para que ninguém amplie o filtro sem perceber.

> Se o teste falhar porque o fake `.in_()` não é suportado, verifique `conftest.py::FakeQuery` — `compute_pendencias` usa `.in_("status", [...])`. O harness já suporta os filtros usados pelo painel; se `in_` faltar, adicione-o ao `FakeQuery` seguindo o padrão de `eq`.

- [ ] **Step 2: Teste da rota no_show do painel**

Em `dashboard/tests/test_main_payments.py`, adicione:

```python
def test_api_pagamentos_no_show_muda_status(client, auth, seed):
    seed["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "doctor_id": JULIO_ID,
         "status": "scheduled", "start_time": "2026-07-01T12:00:00+00:00",
         "end_time": "2026-07-01T13:00:00+00:00",
         "booking_fee_paid_at": None, "booking_fee_waived": False,
         "consultation_type": "acompanhamento", "paid_at": None,
         "patients": {"name": "João"}},
    ]
    resp = client.post("/api/pagamentos/a1/no-show", auth=auth)
    assert resp.status_code == 200
    assert seed["appointments"][0]["status"] == "no_show"
```

Run: `cd dashboard && uv run pytest tests/test_main_payments.py -k no_show -v`
Expected: FAIL (404).

- [ ] **Step 3: Implementar a rota**

Em `dashboard/main.py`, perto das rotas de `/api/pagamentos/...`:

```python
@app.post("/api/pagamentos/{appointment_id}/no-show")
async def api_pagamentos_no_show(appointment_id: str, username: str = Depends(verify_credentials)):
    client = get_supabase()
    await return_reminders.mark_no_show(client, appointment_id)
    return {"ok": True}
```

> `mark_no_show` vive em `return_reminders.py` (Task 3) e é a única fonte da verdade para "marcar falta" — reutilizada pelas duas superfícies. `main.py` já importa `return_reminders`.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_main_payments.py tests/test_payments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/main.py dashboard/tests/test_payments.py dashboard/tests/test_main_payments.py
git commit -m "feat(pagamentos): rota no_show + regressão de compute_pendencias"
```

---

## Task 7: Botão "Não compareceu" no painel de pagamentos

**Files:**
- Modify: `dashboard/templates/pagamentos.html`

- [ ] **Step 1: Ler o template**

Abra `dashboard/templates/pagamentos.html`, localize o loop das pendências (`{% for pend in pendencias %}`) e o botão de pagar existente. Cada pendência expõe `pend.appointment_id`.

- [ ] **Step 2: Adicionar o botão ao lado do de pagar**

```html
<button type="button"
        onclick="marcarNoShowPag('{{ pend.appointment_id }}', this)">
  Não compareceu
</button>
```

E no `<script>`:

```javascript
async function marcarNoShowPag(appointmentId, btn) {
  if (!confirm('Marcar como NÃO COMPARECEU? A pendência sai da lista e a taxa paga fica retida.')) return;
  btn.disabled = true;
  const r = await fetch(`/api/pagamentos/${appointmentId}/no-show`, { method: 'POST' });
  if (r.ok) { location.reload(); } else { btn.disabled = false; alert('Falha ao marcar.'); }
}
```

> O painel embutido no Chatwoot compartilha `compute_pendencias`; confirme se ele usa o mesmo template `pagamentos.html` ou um parcial próprio. Se for um template/JS separado do painel da atendente, replique o botão lá também (mesma rota). Verifique em `dashboard/main.py` qual template a rota do painel embutido renderiza.

- [ ] **Step 3: Smoke test**

Run: `cd dashboard && uv run pytest tests/test_main_payments.py -v`
Expected: PASS (GET do painel ainda renderiza).

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/pagamentos.html
git commit -m "feat(pagamentos): botão Não compareceu na pendência"
```

---

## Task 8: `send_return_reminders` ignora `alta`

**Files:**
- Modify: `scripts/send_return_reminders.py`
- Test: `tests/test_return_reminders_script.py` (Create, se não existir cobertura de `pending_template`)

- [ ] **Step 1: Escrever o teste que falha**

Verifique primeiro se já há testes de `pending_template` (grep `pending_template` em `tests/`). Se não houver, crie `tests/test_return_reminders_script.py`:

```python
from datetime import date

import importlib

send_return_reminders = importlib.import_module("scripts.send_return_reminders")


def test_pending_template_alta_nunca_dispara_mesmo_sem_next_return_date():
    row = {
        "return_interval": "alta",
        "next_return_date": None,  # alta não tem data — não pode dar crash
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
    }
    assert send_return_reminders.pending_template(date(2026, 8, 11), row) is None


def test_pending_template_intervalo_normal_ainda_dispara():
    row = {
        "return_interval": "1_mes",
        "next_return_date": "2026-09-11",
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
    }
    # agosto é o mês anterior a setembro -> retorno_mes_anterior
    assert send_return_reminders.pending_template(date(2026, 8, 11), row) == (
        "retorno_mes_anterior", "month_before_sent_at",
    )
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_return_reminders_script.py -v`
Expected: FAIL — `pending_template` faz `date.fromisoformat(None)` e levanta `TypeError` no caso `alta`.

- [ ] **Step 3: Adicionar o guard**

Em `scripts/send_return_reminders.py::pending_template`, logo após o guard de `"15_dias"` (linha ~62), antes de `date.fromisoformat(row["next_return_date"])`:

```python
    if row.get("return_interval") == "alta":
        return None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_return_reminders_script.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_return_reminders.py tests/test_return_reminders_script.py
git commit -m "feat(retornos): alta não dispara lembrete de retorno"
```

---

## Task 9: `complete_appointments` pula pós-consulta na alta

**Files:**
- Modify: `scripts/complete_appointments.py`
- Test: `tests/test_complete_appointments.py` (adicionar caso; verifique se o arquivo existe — senão, crie seguindo o padrão de mocks dos outros testes de script)

- [ ] **Step 1: Escrever o teste que falha**

O envio de pós-consulta acontece em `_process_pos_consulta`. Ele já pula em vários casos (`_should_skip_unconfirmed`, já tem consulta futura, sem contato). Vamos adicionar: pular se o paciente teve alta naquela consulta. Teste (adapte ao harness de mocks já usado nos testes de script — provavelmente `unittest.mock` + fake client):

```python
import asyncio
from unittest.mock import AsyncMock, patch

import scripts.complete_appointments as ca


def test_process_pos_consulta_pula_se_alta(monkeypatch):
    # Fake client: retorna uma linha de return_reminders com alta para este appt.
    class _Q:
        def __init__(self, table, store): self.table, self.store, self._f = table, store, {}
        def select(self, *a, **k): return self
        def eq(self, c, v): self._f[c] = v; return self
        def gt(self, *a, **k): return self
        def limit(self, *a, **k): return self
        async def execute(self):
            if self.table == "return_reminders":
                return type("R", (), {"data": [{
                    "return_interval": "alta",
                    "last_classified_appointment_id": "a1",
                }]})()
            return type("R", (), {"data": []})()
        def update(self, payload): self._f["_update"] = payload; return self
    class _C:
        def __init__(self): self.store = {}
        def from_(self, t): return _Q(t, self.store)

    sent = AsyncMock()
    monkeypatch.setattr(ca, "send_pos_consulta", sent)
    appt = {"id": "row1", "appointment_id": "a1", "patient_id": "p1",
            "consultation_type": "acompanhamento", "patients": {"name": "João"},
            "confirmed_at": "2026-07-01T00:00:00+00:00",
            "reminder_day_before_sent_at": None}
    asyncio.run(ca._process_pos_consulta(_C(), appt, "2026-07-02T00:00:00+00:00"))
    sent.assert_not_called()
```

> Ajuste ao estilo real do arquivo de teste existente. O ponto essencial: quando há linha `return_reminders` com `return_interval="alta"` e `last_classified_appointment_id == appt["appointment_id"]`, `send_pos_consulta` **não** é chamado.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_complete_appointments.py -k alta -v`
Expected: FAIL (`send_pos_consulta` chamado).

- [ ] **Step 3: Implementar o skip**

Em `scripts/complete_appointments.py::_process_pos_consulta`, adicione uma checagem de alta logo após pegar `patient_id` (antes de `_should_skip_unconfirmed`):

```python
    # Alta: se esta consulta já foi classificada como alta pelo médico, não
    # mande "agende a próxima" — seria contraditório.
    appt_id = appt.get("appointment_id")
    if patient_id and appt_id:
        rr = await (
            client.from_("return_reminders")
            .select("return_interval, last_classified_appointment_id")
            .eq("patient_id", patient_id)
            .execute()
        )
        for row in (rr.data or []):
            if row.get("return_interval") == "alta" and row.get("last_classified_appointment_id") == appt_id:
                print(f"Skipping pos_consulta for patient {patient_id} — alta registrada.")
                await _mark_sent()
                return
```

> `_mark_sent` é a closure já definida no início de `_process_pos_consulta`; a inserção precisa vir **depois** da definição dela. Confira a ordem no arquivo e posicione o bloco logo após `async def _mark_sent(): ...`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_complete_appointments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/complete_appointments.py tests/test_complete_appointments.py
git commit -m "feat(pos-consulta): pula 'agende a próxima' quando houve alta"
```

---

## Task 10: Cron da mensagem de falta

**Files:**
- Create: `scripts/send_no_show_messages.py`
- Test: `tests/test_send_no_show_messages.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
import asyncio
from unittest.mock import AsyncMock

import scripts.send_no_show_messages as sns


def _fake_client(appointments):
    class _Q:
        def __init__(self, table, store): self.table, self.store, self._f = table, store, {}
        def select(self, *a, **k): return self
        def eq(self, c, v): self._f[c] = v; return self
        def is_(self, c, v): self._f[c] = ("is", v); return self
        def lt(self, *a, **k): return self
        def update(self, payload): self._f["_update"] = payload; return self
        async def execute(self):
            if self.table != "appointments":
                return type("R", (), {"data": []})()
            if "_update" in self._f:
                for a in self.store["appointments"]:
                    if a["id"] == self._f.get("id"):
                        a.update(self._f["_update"])
                return type("R", (), {"data": []})()
            rows = [a for a in self.store["appointments"]
                    if a["status"] == "no_show" and a.get("no_show_message_sent_at") is None]
            return type("R", (), {"data": rows})()
    class _C:
        def __init__(self): self.store = {"appointments": appointments}
        def from_(self, t): return _Q(t, self.store)
    return _C()


def test_envia_so_para_no_show_sem_flag(monkeypatch):
    appts = [
        {"id": "r1", "appointment_id": "a1", "patient_id": "p1",
         "status": "no_show", "no_show_message_sent_at": None,
         "start_time": "2026-07-01T12:00:00+00:00", "patients": {"name": "João Silva"}},
        {"id": "r2", "appointment_id": "a2", "patient_id": "p2",
         "status": "no_show", "no_show_message_sent_at": "2026-07-02T00:00:00+00:00",
         "start_time": "2026-07-01T12:00:00+00:00", "patients": {"name": "Maria"}},
    ]
    client = _fake_client(appts)
    monkeypatch.setattr(sns, "acreate_client_for_test", None, raising=False)
    send = AsyncMock()
    monkeypatch.setattr(sns, "send_no_show_message", send)
    monkeypatch.setattr(sns, "get_contacts_for_patient",
                        AsyncMock(return_value=[{"phone": "5581999999999"}]))

    asyncio.run(sns.process(client))

    assert send.await_count == 1  # só p1
    # flag marcada em a1
    assert appts[0]["no_show_message_sent_at"] is not None
    assert appts[1]["no_show_message_sent_at"] == "2026-07-02T00:00:00+00:00"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_send_no_show_messages.py -v`
Expected: FAIL (módulo não existe).

- [ ] **Step 3: Implementar o cron**

Crie `scripts/send_no_show_messages.py`, espelhando `scripts/complete_appointments.py`:

```python
"""
Envia a mensagem de falta (no-show) via WhatsApp.
Roda 1x/dia via GitHub Actions.

Processa appointments com:
  - status = 'no_show'
  - no_show_message_sent_at IS NULL

Independe de quando a falta foi marcada (médico/atendente podem marcar dias
depois). Mensagem acolhedora convidando a remarcar — NÃO menciona taxa (o
aviso de retenção só aparece se o paciente topar remarcar, no bot).
"""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega antes de patients (evita import circular)
from app.patients import get_contacts_for_patient
from app.utils import display_name as _dn


async def send_no_show_message(phone: str, first_name: str) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    content = (
        f"Olá! Notamos que {first_name} não conseguiu comparecer à consulta. "
        f"Se quiser remarcar, é só responder por aqui que a gente te ajuda."
    )
    await send_template_message(
        conv_id,
        template_name="no_show",
        language="pt_BR",
        category="MARKETING",
        body_params={"1": first_name},
        content=content,
    )


async def process(client) -> int:
    result = await (
        client.from_("appointments")
        .select("id, appointment_id, patient_id, status, no_show_message_sent_at, "
                "start_time, patients(name)")
        .eq("status", "no_show")
        .is_("no_show_message_sent_at", "null")
        .execute()
    )
    appointments = result.data or []
    now_iso = datetime.now(timezone.utc).isoformat()
    count = 0
    for appt in appointments:
        patient_id = appt.get("patient_id")
        name = (appt.get("patients") or {}).get("name") or "paciente"
        first_name = _dn(name) if name else "paciente"
        contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
        sent_any = False
        for contact in contacts:
            phone = contact.get("phone")
            if not phone:
                continue
            try:
                await send_no_show_message(phone, first_name)
                sent_any = True
                print(f"No-show message sent to {phone} for appt {appt['appointment_id']}")
            except Exception as e:
                print(f"Failed to send no-show message to {phone}: {e}")
        # Marca a flag mesmo sem contato/sucesso parcial? Só marca se enviou a
        # algum contato — assim um paciente sem contato de 'consulta' é
        # retentado amanhã (mesma postura do pos_consulta).
        if sent_any:
            await (
                client.from_("appointments")
                .update({"no_show_message_sent_at": now_iso})
                .eq("id", appt["id"])
                .execute()
            )
            count += 1
    print(f"Sent {count} no-show message(s).")
    return count


async def main():
    from supabase import acreate_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)
    await process(client)


if __name__ == "__main__":
    asyncio.run(main())
```

> **Decisão de marcação da flag:** o teste do Step 1 assume que a flag é marcada quando há envio. No teste, `get_contacts_for_patient` retorna um contato, então `sent_any=True`. Mantenha a semântica "só marca se enviou". Se preferir marcar sempre (evitar retentar eternamente um paciente sem contato), ajuste o teste junto.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_send_no_show_messages.py -v`
Expected: PASS.

- [ ] **Step 5: Registrar o template `no_show` na Meta**

> Ação fora do código: o template WhatsApp `no_show` (pt_BR, 1 variável = primeiro nome) precisa ser criado e aprovado no WhatsApp Manager, como os outros (`pos_consulta`, `retorno_*`). Anote como pendência de operação; o cron falha graciosamente (log) até o template existir.

- [ ] **Step 6: Commit**

```bash
git add scripts/send_no_show_messages.py tests/test_send_no_show_messages.py
git commit -m "feat(no-show): cron da mensagem de falta"
```

---

## Task 11: Bot avisa da retenção quando paciente no_show pede remarcação

**Files:**
- Modify: `app/graph/tools.py` (helper de detecção) e `app/graph/prompts.py` (instrução)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Entender o ponto de decisão atual**

Leia `app/graph/tools.py:1460-1525` (fluxo de reagendamento e a mensagem "taxa recolhida + nova taxa"). Note como o bot localiza o appointment do paciente. O objetivo: quando o paciente tem uma consulta recente `no_show` e pede para remarcar, o bot deve tratar como **nova reserva com nova taxa** e explicar que a taxa da falta foi retida — nunca como remarcação gratuita.

- [ ] **Step 2: Escrever o teste que falha**

Em `tests/test_tools.py`, adicione um teste que cobre o helper de detecção de no_show recente (função pura, fácil de testar). Primeiro defina o comportamento do helper:

```python
import pytest
from app.graph import tools


@pytest.mark.asyncio
async def test_has_recent_no_show_true(monkeypatch):
    async def fake_maybe(*a, **k):
        class R: data = [{"appointment_id": "a1", "start_time": "2026-08-01T12:00:00+00:00"}]
        return R()
    # Substitua a query do supabase pelo fake (ajuste ao helper de mock já
    # usado em test_tools.py para o client do supabase).
    monkeypatch.setattr(tools, "_supabase_select_no_show", fake_maybe, raising=False)
    assert await tools.has_recent_no_show("p1") is True


@pytest.mark.asyncio
async def test_has_recent_no_show_false(monkeypatch):
    async def fake_maybe(*a, **k):
        class R: data = []
        return R()
    monkeypatch.setattr(tools, "_supabase_select_no_show", fake_maybe, raising=False)
    assert await tools.has_recent_no_show("p1") is False
```

> Ajuste os nomes ao padrão de mock de supabase já usado em `tests/test_tools.py` (o arquivo já mocka o client). O contrato do helper: `has_recent_no_show(patient_id) -> bool`, True se existe appointment com `status='no_show'` para o paciente.

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py -k no_show -v`
Expected: FAIL (`has_recent_no_show` não existe).

- [ ] **Step 4: Implementar o helper**

Em `app/graph/tools.py`, adicione:

```python
async def has_recent_no_show(patient_id: str) -> bool:
    """True se o paciente tem alguma consulta marcada como falta (no_show).

    Usado no fluxo de remarcação: quem faltou não recebe remarcação gratuita;
    a taxa anterior foi retida e uma nova taxa de reserva é cobrada.
    """
    client = await _get_client()  # use o mesmo acesso ao client dos outros tools
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time")
        .eq("patient_id", patient_id)
        .eq("status", "no_show")
        .limit(1)
        .execute()
    )
    return bool(result.data)
```

> Ajuste `_get_client()` para o acessor real usado pelos outros tools deste arquivo (grep por `from_(` em `tools.py` para ver o padrão exato — pode ser um client global ou obtido de `state`).

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k no_show -v`
Expected: PASS.

- [ ] **Step 6: Ligar ao fluxo de remarcação (prompt)**

Em `app/graph/prompts.py`, na seção que instrui o bot sobre remarcação/taxa, adicione uma instrução explícita (texto, reusando o padrão de `tools.py:1519`):

```
- Se o paciente tiver uma consulta anterior marcada como FALTA (no_show) e
  quiser remarcar: a taxa da consulta em que ele faltou foi RETIDA. Trate
  como uma nova reserva — informe que será cobrada uma NOVA taxa de reserva
  de R$ 100,00 para a nova data. NÃO ofereça remarcação gratuita.
```

E, no ponto onde o bot decide sobre a taxa de remarcação (`tools.py`, próximo à checagem das 24h), chame `has_recent_no_show(patient_id)` e, se True, retorne a mesma instrução interna de "taxa recolhida + nova taxa" que já existe para o caso fora do prazo (linha ~1517-1525), adaptando o texto para mencionar a falta.

- [ ] **Step 7: Teste de integração do fluxo (mocked)**

Se `tests/test_process_message.py` já exercita o fluxo de remarcação, adicione um caso: paciente com `no_show` pede remarcação → resposta do bot menciona nova taxa de R$100 e **não** oferece remarcação gratuita. Use o mesmo harness de mock de OpenAI/supabase do arquivo.

Run: `uv run pytest tests/test_process_message.py -k no_show -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/graph/tools.py app/graph/prompts.py tests/test_tools.py tests/test_process_message.py
git commit -m "feat(bot): remarcação após falta cobra nova taxa (retenção)"
```

---

## Task 12: Agendar o cron no GitHub Actions

**Files:**
- Modify/Create: `.github/workflows/` (localize o workflow que roda `complete_appointments.py` / `send_return_reminders.py`)

- [ ] **Step 1: Localizar o workflow existente**

Run: `grep -rl "send_return_reminders\|complete_appointments" .github/workflows/`
Leia o workflow: como ele instala deps (uv), passa secrets (SUPABASE_URL/KEY, tokens da Meta) e agenda (`schedule: cron`).

- [ ] **Step 2: Adicionar o passo/job do novo cron**

Adicione um passo que roda uma vez ao dia (ex.: `cron: "0 12 * * *"`, ajustando ao fuso já usado pelos outros), com os mesmos secrets:

```yaml
      - name: Send no-show messages
        run: uv run python -m scripts.send_no_show_messages
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          # + demais secrets da Meta/Chatwoot usados por send_return_reminders
```

> Copie a lista exata de `env:` do passo de `send_return_reminders` (mesmas dependências de envio de WhatsApp). Se cada cron é um workflow próprio, crie `.github/workflows/send-no-show-messages.yml` espelhando o de return reminders.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/
git commit -m "ci: agenda cron da mensagem de falta"
```

---

## Verificação final

- [ ] **Rodar toda a suíte do dashboard**

Run: `cd dashboard && uv run pytest --tb=short`
Expected: PASS.

- [ ] **Rodar toda a suíte do app/scripts**

Run: `uv run pytest --tb=short`
Expected: PASS. (Atenção à ordem de imports — ver memória "Pytest: ordem dos arquivos e import circular": não passe `test_patients.py` como primeiro argumento.)

- [ ] **Revisar a seção "Fora de escopo" do spec com o usuário** (pendência combinada): retração de pós-consulta já enviado; automação de reembolso no no_show; política de crédito/reaproveitamento da taxa retida.

---

## Notas de risco / verificar durante execução

1. **Painel embutido no Chatwoot** (Task 7): confirmar se usa `pagamentos.html` ou um template/JS próprio; o botão precisa aparecer na superfície que a atendente realmente usa.
2. **Acesso ao client do supabase no `app/graph/tools.py`** (Task 11): usar o mesmo padrão dos tools vizinhos, não introduzir um novo.
3. **Template `no_show` na Meta** (Task 10): precisa ser aprovado antes de o cron enviar de verdade.
4. **Branch `05bacb7`** (scheduled-past na fila) não está neste branch; se for mesclado depois, a exclusão defensiva de `no_show` da Task 3 já cobre. Se a fila passar a incluir `scheduled` passada, garanta que a mesma exclusão valha lá.
