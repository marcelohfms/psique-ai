# Data de retorno no painel da atendente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exibir e permitir editar a "data de retorno" (`return_reminders.next_return_date`) na aba Paciente do painel da atendente embutido no Chatwoot.

**Architecture:** O painel `dashboard/` é autocontido (não importa `app/`) e fala direto com o Supabase. Adicionamos uma leitura (`get_return_reminder`) e uma escrita restrita (`update_return_reminder`, só UPDATE, que zera as flags de envio para o cron de lembretes se realinhar), expostas por rotas em `attendant_routes.py`, e um bloco novo no template `atendente.html`.

**Tech Stack:** Python 3 / FastAPI / postgrest-py (Supabase) / Jinja2 + JS vanilla. Testes com pytest e o `FakeClient` em `dashboard/tests/conftest.py`.

---

## Contexto essencial para quem implementa

- A data de retorno **não** fica em `patients`; fica na tabela `return_reminders` (1 linha por paciente): colunas relevantes `patient_id`, `doctor_id`, `return_interval`, `next_return_date` (DATE, pode ser NULL no caso `alta`), e as flags `month_before_sent_at` / `month_of_sent_at` / `overdue_sent_at`.
- Quem grava hoje é a página `/retornos` (médicos), via `dashboard/return_reminders.py::save_classification`. O bot `app/` **nunca** toca nessa tabela. **Não** mexer em `app/` nem na página `/retornos`.
- Decisões de design (spec `docs/superpowers/specs/2026-08-12-data-retorno-painel-atendente-design.md`):
  - **Só editar linha existente** (nada de criar do zero / sem migration). Se não há linha, o `UPDATE` não afeta nada.
  - Ao salvar a nova data, **zerar as 3 flags de envio** para o cron `scripts/send_return_reminders.py` re-agendar na nova data.
  - Caso `return_interval == 'alta'`: mostrar a nota, campo só leitura (sem data).
- O cliente Supabase do dashboard vem de `from db_client import get_client` (assíncrono: `client = await get_client()`).
- Padrão de teste: fixture `patched_client` faz monkeypatch de `attendant_db.get_client`; `patched_client.store["<tabela>"]` é a lista de linhas. Ver `dashboard/tests/test_attendant_db.py`.

## Estrutura de arquivos

- Modificar: `dashboard/attendant_db.py` — imports de tempo, `_RETURN_FIELDS`, `get_return_reminder`, `update_return_reminder`.
- Modificar: `dashboard/attendant_routes.py` — incluir `return_reminder` na rota de leitura; nova rota de gravação.
- Modificar: `dashboard/templates/atendente.html` — bloco "Data de retorno" na aba Paciente.
- Testes: `dashboard/tests/test_attendant_db.py`, `dashboard/tests/test_attendant_routes.py`.

Rodar testes: `cd dashboard && uv run pytest tests/ --tb=short` (o `dashboard/` tem seu próprio venv/uv).

---

### Task 1: `get_return_reminder` (leitura da linha de retorno)

**Files:**
- Modify: `dashboard/attendant_db.py` (após `get_link`, ~linha 82)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing test**

Adicionar em `dashboard/tests/test_attendant_db.py` (no bloco de leitura, após `test_get_patient_missing`):

```python
# ── Leitura da data de retorno ────────────────────────────────────────────────


async def test_get_return_reminder_found(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "doctor_id": "d1",
         "return_interval": "2_meses", "next_return_date": "2026-09-15"},
    ]
    out = await attendant_db.get_return_reminder("p1")
    assert out["return_interval"] == "2_meses"
    assert out["next_return_date"] == "2026-09-15"


async def test_get_return_reminder_missing(patched_client):
    assert await attendant_db.get_return_reminder("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py::test_get_return_reminder_found -v`
Expected: FAIL — `AttributeError: module 'attendant_db' has no attribute 'get_return_reminder'`

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/attendant_db.py`, logo após a função `get_link` (linha ~82):

```python
async def get_return_reminder(patient_id: str) -> dict | None:
    """Linha de return_reminders do paciente (1 por paciente) ou None.

    A data de retorno mora nesta tabela separada, não em `patients`.
    """
    client = await get_client()
    res = (
        await client.from_("return_reminders")
        .select("next_return_date, return_interval, doctor_id")
        .eq("patient_id", patient_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -k return_reminder -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): leitura da data de retorno (get_return_reminder)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `update_return_reminder` (gravar data + zerar flags)

**Files:**
- Modify: `dashboard/attendant_db.py` (imports no topo; whitelist junto das outras ~linha 93; função junto dos updates ~linha 121)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing test**

Adicionar em `dashboard/tests/test_attendant_db.py` (após os testes da Task 1):

```python
# ── Escrita da data de retorno ────────────────────────────────────────────────


async def test_update_return_reminder_sets_date_and_resets_flags(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "return_interval": "2_meses",
         "next_return_date": "2026-09-15",
         "month_before_sent_at": "2026-08-01T00:00:00-03:00",
         "month_of_sent_at": None, "overdue_sent_at": None},
    ]
    updated = await attendant_db.update_return_reminder("p1", {"next_return_date": "2026-10-15"})
    assert updated is True
    row = patched_client.store["return_reminders"][0]
    assert row["next_return_date"] == "2026-10-15"
    assert row["month_before_sent_at"] is None
    assert row["month_of_sent_at"] is None
    assert row["overdue_sent_at"] is None
    assert row["updated_at"]  # timestamp preenchido


async def test_update_return_reminder_no_row_returns_false(patched_client):
    patched_client.store["return_reminders"] = []
    updated = await attendant_db.update_return_reminder("p1", {"next_return_date": "2026-10-15"})
    assert updated is False
    assert patched_client.store["return_reminders"] == []  # não cria linha


async def test_update_return_reminder_empty_data_noop(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "next_return_date": "2026-09-15"},
    ]
    updated = await attendant_db.update_return_reminder("p1", {"foo": "bar"})
    assert updated is False
    assert patched_client.store["return_reminders"][0]["next_return_date"] == "2026-09-15"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py::test_update_return_reminder_sets_date_and_resets_flags -v`
Expected: FAIL — `AttributeError: module 'attendant_db' has no attribute 'update_return_reminder'`

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/attendant_db.py`, no topo, trocar o import atual por (adiciona datetime + zoneinfo):

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from db_client import get_client

_TZ = ZoneInfo("America/Recife")
```

Adicionar a whitelist junto das outras (após `_LINK_FIELDS`, ~linha 93):

```python
_RETURN_FIELDS = {"next_return_date"}
```

Adicionar a função junto dos outros updates (após `update_link`, ~linha 121):

```python
async def update_return_reminder(patient_id: str, data: dict) -> bool:
    """Atualiza a data de retorno do paciente e zera as flags de envio.

    Só faz UPDATE (não cria linha): se o paciente ainda não foi classificado
    pela médica, nada acontece e retorna False. Zerar as flags realinha o cron
    `scripts/send_return_reminders.py` para disparar os lembretes na nova data.
    """
    payload = _filter(data, _RETURN_FIELDS)
    if not payload:
        return False
    payload["month_before_sent_at"] = None
    payload["month_of_sent_at"] = None
    payload["overdue_sent_at"] = None
    payload["updated_at"] = datetime.now(_TZ).isoformat()
    client = await get_client()
    res = (
        await client.from_("return_reminders")
        .update(payload)
        .eq("patient_id", patient_id)
        .execute()
    )
    return bool(res.data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -v`
Expected: PASS (todos, incluindo os 3 novos)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): gravar data de retorno e zerar flags (update_return_reminder)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: incluir `return_reminder` na rota de leitura do paciente

**Files:**
- Modify: `dashboard/attendant_routes.py:47-53` (rota `paciente`)
- Test: `dashboard/tests/test_attendant_routes.py`

- [ ] **Step 1: Write the failing test**

Adicionar em `dashboard/tests/test_attendant_routes.py` (após `test_get_patient_ok`):

```python
def test_get_patient_includes_return_reminder(client, monkeypatch):
    async def fake_get_patient(pid):
        return {"id": "p1", "name": "João"}
    async def fake_get_link(pid, cid):
        return {"id": "pc1"}
    async def fake_get_rr(pid):
        return {"next_return_date": "2026-09-15", "return_interval": "2_meses", "doctor_id": "d1"}
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "get_link", fake_get_link)
    monkeypatch.setattr(attendant_db, "get_return_reminder", fake_get_rr)
    r = client.get("/api/atendente/paciente/p1",
                   params={"contact_id": "c1", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["return_reminder"]["next_return_date"] == "2026-09-15"
    assert body["return_reminder"]["return_interval"] == "2_meses"
```

Também atualizar o `test_get_patient_ok` existente para stubar `get_return_reminder` (senão ele chama o real e quebra). Adicionar dentro dele, junto dos outros monkeypatch:

```python
    async def fake_get_rr(pid):
        return None
    monkeypatch.setattr(attendant_db, "get_return_reminder", fake_get_rr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py::test_get_patient_includes_return_reminder -v`
Expected: FAIL — `KeyError: 'return_reminder'` (a chave ainda não está no JSON)

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/attendant_routes.py`, substituir a rota `paciente` (linhas 47-53):

```python
@router.get("/paciente/{patient_id}")
async def paciente(patient_id: str, contact_id: str, _: None = Depends(verify_token)):
    patient = await attendant_db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="paciente não encontrado")
    link = await attendant_db.get_link(patient_id, contact_id)
    return_reminder = await attendant_db.get_return_reminder(patient_id)
    return {"patient": patient, "link": link, "return_reminder": return_reminder}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -k patient -v`
Expected: PASS (incluindo `test_get_patient_ok` e o novo)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py
git commit -m "feat(painel): incluir return_reminder na leitura do paciente

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: rota de gravação `POST /paciente/{id}/retorno`

**Files:**
- Modify: `dashboard/attendant_routes.py` (import `date`; nova rota após `update_paciente`, ~linha 72)
- Test: `dashboard/tests/test_attendant_routes.py`

- [ ] **Step 1: Write the failing test**

Adicionar em `dashboard/tests/test_attendant_routes.py` (no bloco de escrita, após `test_update_patient_requires_token`):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py::test_update_return_date_ok -v`
Expected: FAIL — 404 (rota ainda não existe)

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/attendant_routes.py`, ajustar o import de datetime no topo (linha ~8). Trocar:

```python
import os
```

por:

```python
import os
from datetime import date as _date
```

Adicionar a rota logo após `update_paciente` (após a linha 72):

```python
@router.post("/paciente/{patient_id}/retorno")
async def update_return_date(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    """Atualiza a data de retorno do paciente (tabela return_reminders).

    Só edita retorno já classificado pela médica; realinha os lembretes.
    """
    raw = body.data.get("next_return_date")
    if not raw:
        raise HTTPException(status_code=400, detail="next_return_date obrigatório")
    try:
        _date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="next_return_date deve ser YYYY-MM-DD")
    updated = await attendant_db.update_return_reminder(patient_id, {"next_return_date": raw})
    await attendant_db.log_event("attendant_edit_return_date", body.phone,
                                 {"patient_id": patient_id, "next_return_date": raw, "updated": updated})
    return {"ok": True, "updated": updated}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -k return_date -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py
git commit -m "feat(painel): rota para editar data de retorno da atendente

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: bloco "Data de retorno" no template `atendente.html`

**Files:**
- Modify: `dashboard/templates/atendente.html` (mapa de labels + `returnBlock`; assinatura/chamadas de `renderForms`; injeção no `pacienteHtml`; função `saveRetorno`)

Sem teste automatizado (templates/JS não têm harness no repo). Verificação manual no Step final.

- [ ] **Step 1: Adicionar o mapa de labels e a função `returnBlock`**

Em `dashboard/templates/atendente.html`, logo após a constante `DOCTORS` (linha 296-298), adicionar:

```javascript
const RETURN_LABELS = {
  "15_dias": "15 dias", "1_mes": "1 mês", "2_meses": "2 meses",
  "3_meses": "3 meses", "4_meses": "4 meses", "6_meses": "6 meses",
};

function returnBlock(pid, rr) {
  const wrap = (inner, accent) => `<div class="mt-2 rounded-md border ${accent ? "border-teal-300 bg-teal-50 dark:border-teal-700 dark:bg-teal-900/30" : "border-gray-200 dark:border-gray-700"} p-3">
    <div class="text-xs uppercase ${accent ? "text-teal-700 dark:text-teal-300" : "text-gray-400 dark:text-gray-500"} mb-1">Data de retorno</div>${inner}</div>`;
  if (!rr) {
    return wrap(`<div class="text-sm text-gray-500 dark:text-gray-400">— <span class="text-xs text-gray-400 dark:text-gray-500">(nenhum retorno classificado ainda)</span></div>`, false);
  }
  if (rr.return_interval === "alta") {
    return wrap(`<div class="text-sm text-gray-500 dark:text-gray-400">— <span class="text-xs">paciente recebeu alta</span></div>`, false);
  }
  const label = RETURN_LABELS[rr.return_interval] || rr.return_interval || "";
  const dateVal = rr.next_return_date || "";
  const nota = label ? `<span class="text-xs text-gray-500 dark:text-gray-400">informado pela médica: retorno em ${escapeHtml(label)}</span>` : "";
  return wrap(`<div class="flex items-center gap-2 flex-wrap">
    <input id="p_return_date" type="date" value="${escapeHtml(dateVal)}" class="border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 rounded px-2 py-1 text-sm">
    <button onclick="saveRetorno('${pid}')" class="bg-teal-600 hover:bg-teal-700 text-white text-xs px-3 py-1.5 rounded">Salvar retorno</button>
    ${nota}
  </div>`, true);
}
```

- [ ] **Step 2: Passar o return_reminder para `renderForms`**

Na função `loadPatient` (linhas 268-273), trocar por:

```javascript
async function loadPatient(pid) {
  const r = await fetch(`/api/atendente/paciente/${pid}?contact_id=${CONTACT.id}&token=${encodeURIComponent(TOKEN)}`);
  if (!r.ok) { setStatus("Erro ao carregar paciente."); return; }
  const { patient, link, return_reminder } = await r.json();
  renderForms(patient, link, return_reminder);
}
```

Na assinatura de `renderForms` (linha 300), trocar `function renderForms(patient, link) {` por:

```javascript
function renderForms(patient, link, returnReminder) {
```

- [ ] **Step 3: Injetar o bloco no `pacienteHtml`**

Dentro de `renderForms`, no `pacienteHtml` (linhas 323-340), inserir o bloco entre a `</div>` que fecha os checkboxes (linha 338) e o botão "Salvar paciente" (linha 339). O trecho fica assim:

```javascript
      <div class="flex flex-wrap gap-4 mt-1 mb-2">
        ${checkbox("Paciente retornante", "p_returning", patient.is_returning_patient)}
        ${checkbox("Exceção de idade", "p_age_exc", patient.age_exception)}
      </div>
      ${returnBlock(patient.id, returnReminder)}
      <button onclick="savePatient('${patient.id}')" class="mt-2 bg-wa-green hover:bg-wa-green-dk text-white text-xs px-3 py-1.5 rounded">Salvar paciente</button>
    </section>`;
```

- [ ] **Step 4: Adicionar `saveRetorno`**

Após a função `saveLink` (linhas 395-400), adicionar:

```javascript
async function saveRetorno(pid) {
  const ok = await post(`/api/atendente/paciente/${pid}/retorno`, { data: {
    next_return_date: val("p_return_date"),
  }});
  if (ok) flash("Data de retorno salva ✓");
}
```

- [ ] **Step 5: Verificação manual no navegador**

Iniciar o dashboard e abrir o painel com um telefone de teste que tenha paciente + linha em `return_reminders`:

```bash
cd dashboard && uv run uvicorn main:app --reload --port 8001
```

Abrir `http://localhost:8001/atendente?token=<ATTENDANT_PANEL_TOKEN>&phone=<telefone_de_teste>`, ir na aba **Paciente** e conferir:
- Bloco "Data de retorno" aparece com a data preenchida, botão "Salvar retorno" e a nota "informado pela médica: retorno em …".
- Paciente sem linha de retorno: mostra "— (nenhum retorno classificado ainda)".
- Editar a data e clicar "Salvar retorno" → status mostra "Data de retorno salva ✓"; recarregar confirma a nova data.

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(painel): exibir e editar data de retorno na aba Paciente

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: suíte completa verde

- [ ] **Step 1: Rodar toda a suíte do dashboard**

Run: `cd dashboard && uv run pytest tests/ --tb=short`
Expected: PASS (todos)

- [ ] **Step 2: Rodar a suíte principal (garantir que nada de `app/` quebrou — não deve, nada foi tocado)**

Run: `uv run pytest --tb=short`
Expected: PASS

- [ ] **Step 3: Commit final (se houver ajuste pendente)**

```bash
git add -A && git commit -m "test: suíte verde para data de retorno no painel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" || echo "nada a commitar"
```

---

## Self-review

- **Cobertura do spec:** exibir data (Task 1 + 3 + 5) ✓; editar + zerar flags (Task 2 + 4 + 5) ✓; só editar existentes / sem migration (Task 2, UPDATE-only) ✓; caso `alta` só leitura (Task 5 `returnBlock`) ✓; nota "informado pela médica" com mapa de intervalos (Task 5) ✓; validação de data no back (Task 4) ✓; testes em `dashboard/tests/` (Tasks 1-4) ✓; sem tocar `app/` nem `/retornos` ✓.
- **Sem placeholders:** todos os steps têm o código real.
- **Consistência de tipos/nomes:** `get_return_reminder(patient_id) -> dict|None`, `update_return_reminder(patient_id, data) -> bool`, chave JSON `return_reminder`, rota `POST /paciente/{id}/retorno`, id do input `p_return_date`, evento de log `attendant_edit_return_date` — usados de forma idêntica entre backend, rota, template e testes.
