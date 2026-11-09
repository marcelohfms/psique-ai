# Reinjetar comprovante dropado — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando a rede de reconciliação achar um comprovante de pagamento que dropou na ingestão, reinjetá-lo no caminho normal da Eva — registrando e avisando a paciente sempre — em vez de só mandar e-mail à clínica.

**Architecture:** `app/receipt_reconciliation.py::reconcile` já lista anexos do Chatwoot, pula os com rastro e classifica. Para `kind == "payment"`, em vez de `_alert_clinic`, passa a chamar um novo `replay_dropped_receipt(cand, img)`, que normaliza o telefone, chama `app/media.describe_image_bytes` (classifica, sobe pro Drive, relê o valor) e, com o texto de comprovante em mãos, grava a linha-espelho e invoca `process_message` com um bypass LOCAL do portão de pausa, deixando `register_payment` (com todas as travas) registrar e a Eva responder. Documento e releitura divergente continuam no `_alert_clinic` de hoje.

**Tech Stack:** Python, FastAPI, LangGraph, OpenAI, Supabase, Chatwoot API. Sem dependência nova.

**Spec:** `docs/superpowers/specs/2026-11-09-reinjetar-comprovante-dropado-design.md`

## Global Constraints

- Sem dependência nova; seguir padrões do repo.
- Testes mockam Chatwoot / Drive / OpenAI / Supabase; rodar com `uv run pytest --tb=short`.
- O bypass do portão de pausa é LOCAL a este fluxo: o caminho normal (`process_message` sem o flag) continua calado em conversa pausada.
- Idempotência: cada `attachment_id` é reinjetado no máximo uma vez.
- Um candidato que lança exceção nunca derruba a rodada do cron.
- Telefone Eva-facing sempre na forma canônica `_phone_variants(<digitos>)[0] + "@s.whatsapp.net"`.

## Review Focus

- **Telefone cru do candidato** (`list_recent_incoming_attachments` devolve dígitos sem sufixo): sem normalizar, abre thread fantasma e o registro vai pro vazio em silêncio → coberto na Task 3 (normalização) e Task 4 (teste i).
- **2ª classificação diverge para documento** (`describe_image_bytes` devolve `None`): não pode mandar "recebemos documento" nem enterrar o comprovante → Task 3 cai no `_alert_clinic`, Task 4 teste h.
- **Cron roda de novo antes do registro deixar rastro**: sem marcador próprio, reinjeta a cada 15 min → Task 1 (`receipt_drop_replayed`) + Task 4 teste c.
- **Mesmo md5 já lançado pela painel em paralelo**: não pode duplicar → travas internas de `register_payment`; Task 4 teste d.
- **Conversa pausada (`manual_hold`/`eva-inativa`)**: tem que registrar E responder, sem vazar o bypass pro caminho normal → Task 2 (flag) + Task 4 testes b e g.

---

### Task 1: Marcador de idempotência `receipt_drop_replayed`

**Files:**
- Modify: `app/receipt_reconciliation.py`
- Test: `tests/test_receipt_reconciliation.py`

**Interfaces:**
- Produces: `REPLAY_EVENT = "receipt_drop_replayed"`; `async def already_replayed(attachment_id: int) -> bool` (mesmo padrão de `already_alerted`, filtra `event_type == REPLAY_EVENT` com `metadata contains {attachment_id}`).

- [ ] **Step 1: Write the failing test**

```python
async def test_already_replayed_true_when_event_exists(monkeypatch):
    # fake supabase: events com receipt_drop_replayed {attachment_id: 2112} → True;
    # attachment_id ausente → False
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_receipt_reconciliation.py::test_already_replayed_true_when_event_exists -v`
Expected: FAIL (`already_replayed` não existe)

- [ ] **Step 3: Implement `already_replayed`**

Adicionar `REPLAY_EVENT = "receipt_drop_replayed"` e `already_replayed(attachment_id)` espelhando `already_alerted` (trocar o `event_type`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_receipt_reconciliation.py::test_already_replayed_true_when_event_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/receipt_reconciliation.py tests/test_receipt_reconciliation.py
git commit -m "feat(reconcile): marcador de idempotência receipt_drop_replayed"
```

---

### Task 2: Bypass local do portão de pausa em `process_message`

**Files:**
- Modify: `app/main.py` (`process_message`, assinatura na linha ~780 e checagem `_eva_paused_for_phone` na ~814)
- Test: `tests/test_process_message.py`

**Interfaces:**
- Produces: `process_message(phone: str, text: str, *, bypass_pause: bool = False) -> None`. Quando `bypass_pause=True`, NÃO retorna cedo em `_eva_paused_for_phone`. Padrão `False` preserva o comportamento atual.

- [ ] **Step 1: Write the failing tests**

```python
async def test_process_message_paused_returns_without_bypass(monkeypatch):
    # _eva_paused_for_phone → True; process_message(phone, "oi")
    # NÃO chama o grafo (chatbot.aget_state/ainvoke não tocado)

async def test_process_message_paused_proceeds_with_bypass(monkeypatch):
    # _eva_paused_for_phone → True; process_message(phone, "texto", bypass_pause=True)
    # PASSA do portão (chega a chamar aget_state)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k bypass -v`
Expected: FAIL (parâmetro inexistente / portão sempre barra)

- [ ] **Step 3: Implement the flag**

Adicionar `*, bypass_pause: bool = False` à assinatura; trocar a guarda para `if not bypass_pause and await _eva_paused_for_phone(phone): return`. Nada mais muda.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k bypass -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_process_message.py
git commit -m "feat(eva): bypass_pause local em process_message"
```

---

### Task 3: `replay_dropped_receipt` — reinjeta o comprovante no caminho da Eva

**Files:**
- Modify: `app/receipt_reconciliation.py`
- Test: `tests/test_receipt_reconciliation.py`

**Interfaces:**
- Consumes: `already_replayed` (Task 1); `process_message(..., bypass_pause=True)` (Task 2); `app/media.describe_image_bytes(image_bytes, phone=...) -> str | None`; `app.main.save_message`; `app.phone._phone_variants`; `_alert_clinic` (já existe).
- Produces: `async def replay_dropped_receipt(cand: dict, img: bytes) -> bool`. Retorna `True` se reinjetou (comprovante lido), `False` se caiu no fallback de alerta. `cand` tem `{attachment_id, phone (dígitos), created_at, data_url, conversation_id}`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_replay_registers_and_notifies(monkeypatch):
    # describe_image_bytes → "COMPROVANTE DE PAGAMENTO: ...[drive_link:...]"
    # replay_dropped_receipt(cand_raw_phone, img) →
    #   - normaliza o phone para "<9digitos>@s.whatsapp.net"
    #   - save_message chamado com o phone canônico e o texto do comprovante
    #   - log_event("receipt_drop_replayed", ...) gravado
    #   - process_message chamado com bypass_pause=True e o phone canônico
    #   - retorna True

async def test_replay_falls_back_to_alert_when_not_a_receipt(monkeypatch):
    # describe_image_bytes → None (reclassificou como documento)
    # replay_dropped_receipt(...) →
    #   - _alert_clinic chamado
    #   - NÃO grava receipt_drop_replayed, NÃO chama process_message
    #   - retorna False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_receipt_reconciliation.py -k replay -v`
Expected: FAIL (`replay_dropped_receipt` não existe)

- [ ] **Step 3: Implement `replay_dropped_receipt`**

Ordem: normalizar `phone` cru → canônico (`_phone_variants(cand["phone"])[0] + "@s.whatsapp.net"`, com fallback ao cru se variants vazio); `texto = await describe_image_bytes(img, phone=canonico)`; se `texto` for falsy → `await _alert_clinic(cand, "payment")` e `return False`; senão `await log_event(REPLAY_EVENT, canonico, {"attachment_id", "conversation_id"})`, `await save_message(canonico, "user", texto)`, `await process_message(canonico, texto, bypass_pause=True)`, `return True`. Import de `save_message`/`process_message` dentro da função (evita import circular com `app.main`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_receipt_reconciliation.py -k replay -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/receipt_reconciliation.py tests/test_receipt_reconciliation.py
git commit -m "feat(reconcile): replay_dropped_receipt reinjeta comprovante na Eva"
```

---

### Task 4: Ligar o `reconcile` ao replay e cobrir os casos

**Files:**
- Modify: `app/receipt_reconciliation.py` (loop de `reconcile`)
- Test: `tests/test_receipt_reconciliation.py`

**Interfaces:**
- Consumes: `already_replayed`, `replay_dropped_receipt` (Tasks 1/3), `_alert_clinic`.
- Produces: comportamento final de `reconcile` — para `kind == "payment"`, se não `already_replayed(aid)`, chama `replay_dropped_receipt(cand, img)`; para `kind == "document"`, mantém `_alert_clinic` + `log_event(RECEIPT_DROP_EVENT, ...)` como hoje. O `try/except` por candidato permanece.

- [ ] **Step 1: Write the failing tests**

```python
async def test_reconcile_payment_calls_replay(monkeypatch): ...        # (a) kind=payment → replay_dropped_receipt chamado
async def test_reconcile_document_still_alerts(monkeypatch): ...        # (e) kind=document → _alert_clinic, replay NÃO chamado
async def test_reconcile_skips_already_replayed(monkeypatch): ...       # (c) already_replayed True → replay NÃO chamado
async def test_reconcile_candidate_exception_does_not_abort(monkeypatch): ...  # (f) replay lança → segue p/ próximo candidato
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_receipt_reconciliation.py -k reconcile -v`
Expected: FAIL (reconcile ainda chama `_alert_clinic` para payment)

- [ ] **Step 3: Wire the loop**

No corpo de `reconcile`, após `kind = classify...`: se `kind == "document"` → ramo atual (`_alert_clinic` + `log_event(RECEIPT_DROP_EVENT)`). Se `kind == "payment"` → `if await already_replayed(aid): continue` senão `await replay_dropped_receipt(cand, img)`. Manter `kind not in ("payment","document") → continue` e o `try/except` por candidato intactos.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/test_receipt_reconciliation.py tests/test_process_message.py --tb=short`
Expected: PASS (inclui regressão: documento só alerta; caminho normal pausado continua calado — teste g já em test_process_message)

- [ ] **Step 5: Commit**

```bash
git add app/receipt_reconciliation.py tests/test_receipt_reconciliation.py
git commit -m "feat(reconcile): reinjeta comprovante (payment) em vez de só alertar"
```

---

### Task 5: Testes de integração dos casos do Review Focus que faltam

**Files:**
- Test: `tests/test_receipt_reconciliation.py`

**Interfaces:**
- Consumes: todo o fluxo das Tasks 1–4.

- [ ] **Step 1: Write the tests**

```python
async def test_replay_in_manual_hold_registers_and_notifies(monkeypatch):
    # (b) contato manual_hold: process_message chamado com bypass_pause=True;
    #     register_payment (mockado) grava e a confirmação sai — nenhuma supressão

async def test_replay_phone_is_normalized(monkeypatch):
    # (i) cand["phone"] cru (12 dígitos sem o 9) → save_message/process_message recebem a forma canônica com 9 + @s.whatsapp.net

async def test_replay_duplicate_md5_does_not_double_register(monkeypatch):
    # (d) register_payment (real ou mockado no ponto de guarda) devolve DUPLICATE_RECEIPT_MARKER;
    #     replay não gera 2º payment_receipt_registered
```

- [ ] **Step 2: Run tests to verify they fail/pass as implemented**

Run: `uv run pytest tests/test_receipt_reconciliation.py -k "manual_hold or normalized or duplicate_md5" -v`
Expected: PASS (comportamento já implementado nas Tasks 1–4; estes testes fixam os casos de borda)

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest --tb=short`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_receipt_reconciliation.py
git commit -m "test(reconcile): casos de borda do replay (manual_hold, telefone, md5)"
```
