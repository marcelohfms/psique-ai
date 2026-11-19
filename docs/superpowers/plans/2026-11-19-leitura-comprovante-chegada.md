# Leitura do comprovante na chegada (insistir no download) — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ler o comprovante em segundos na chegada, insistindo no download do anexo do Chatwoot por ~60s, em vez de esperar a varredura de 10–15 min.

**Architecture:** Um módulo novo `app/receipt_rescue.py` com uma tarefa de resgate que rebaixa o anexo algumas vezes com espera crescente e, quando abre, ingere pelo caminho normal da Eva. O webhook do Chatwoot (`_handle_chatwoot_payload`) agenda essa tarefa em segundo plano quando o processamento do anexo na chegada devolve vazio. A varredura de 15 min fica como rede final.

**Tech Stack:** Python async, FastAPI, httpx, Supabase, OpenAI vision. Reaproveita `describe_image_bytes` (app/media.py), `process_message`/`save_message` (app/main.py), `log_event` (app/database.py) e `has_trace`/`has_payment_trace_until_now`/`already_replayed`/`REPLAY_EVENT` (app/receipt_reconciliation.py).

**Spec:** docs/superpowers/specs/2026-11-19-comprovante-leitura-na-chegada-design.md

## Global Constraints

- Português do Brasil em texto para o usuário; sem bullets e sem travessões em texto que a dona lê.
- Todo trabalho na worktree `.worktrees/leitura-comprovante-chegada`, nunca na `main`.
- TDD: teste antes do código; mocks para Supabase, Chatwoot, OpenAI.
- `uv run pytest --tb=short` precisa passar; CI verde antes do merge.
- Não registrar pagamento fora do fluxo normal: a ingestão passa por `process_message` sem bypass de pausa.
- Commits terminam com `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Review Focus

- Anexo de áudio ou arquivo já tratado (retorna texto) não deve agendar resgate — só imagem/arquivo cujo download falhou; coberto no teste de agendamento da Task 2.
- Conversa fora do inbox da Psique (`CHATWOOT_INBOX_ID`) não deve agendar nem ingerir; coberto no teste de inbox da Task 2.
- Entrega dupla do mesmo anexo (webhook de conta + agent bot, ou retry) não deve agendar dois resgates nem ingerir duas vezes; coberto pela dedup por attachment_id na Task 2 e pela checagem de rastro na Task 1.
- Conversa pausada (`manual_hold`/`eva-inativa`) não deve gerar confirmação automática: o resgate chama `process_message` sem `bypass_pause`; coberto no teste de ingestão da Task 1 (asserção de que não passa bypass).
- Imagem que abre mas não é lida como comprovante nem documento (`describe_image_bytes` devolve None) não deve ingerir nada; coberto no teste de None da Task 1.

---

### Task 1: Módulo de resgate `app/receipt_rescue.py`

**Files:**
- Create: `app/receipt_rescue.py`
- Test: `tests/test_receipt_rescue.py`

**Interfaces:**
- Consumes: `describe_image_bytes(image_bytes, phone="", *, payment_only=False) -> str | None`; `log_event(event_type, phone, metadata)`; `save_message(phone, role, content)`; `process_message(phone, text, *, bypass_pause=False)`; `has_trace(phone, created_at) -> bool`; `has_payment_trace_until_now(phone, created_at) -> bool`; `already_replayed(attachment_id) -> bool`; `REPLAY_EVENT`; `_phone_variants(phone)`.
- Produces: `async def rescue_incoming_attachment(*, phone: str, conversation_id: int, attachment_id: int, data_url: str, created_at: int, sleep=asyncio.sleep) -> bool`. Constantes `RESCUE_DOWNLOAD_DELAYS: tuple[int, ...]` e `RESCUED_EVENT: str`.

- [ ] **Step 1: Escrever os testes que falham** em `tests/test_receipt_rescue.py`

```python
# Mock: app.receipt_rescue._download_bytes, describe_image_bytes, save_message,
# process_message, log_event, has_trace, has_payment_trace_until_now, already_replayed.
# sleep=AsyncMock para não esperar de verdade.

async def test_retries_download_then_ingests():
    # _download_bytes: 1ª chamada levanta httpx error, 2ª devolve b"img"
    # describe_image_bytes -> "[imagem]: COMPROVANTE DE PAGAMENTO: ..."
    # has_trace/has_payment_trace_until_now/already_replayed -> False
    ok = await rescue_incoming_attachment(phone="5581999348383", conversation_id=542,
            attachment_id=2242, data_url="http://x/2242", created_at=1795092787, sleep=AsyncMock())
    assert ok is True
    save_message.assert_awaited_once()
    # respeita a pausa: process_message chamado sem bypass_pause=True
    _, kwargs = process_message.await_args
    assert kwargs.get("bypass_pause", False) is False
    # idempotência e observabilidade
    logged = {c.args[0] for c in log_event.await_args_list}
    assert REPLAY_EVENT in logged and RESCUED_EVENT in logged

async def test_skips_when_already_traced():
    # has_trace -> True
    ok = await rescue_incoming_attachment(..., sleep=AsyncMock())
    assert ok is False
    _download_bytes.assert_not_called()
    process_message.assert_not_awaited()

async def test_gives_up_after_max_attempts():
    # _download_bytes sempre levanta; sleep=AsyncMock
    ok = await rescue_incoming_attachment(..., sleep=AsyncMock())
    assert ok is False
    process_message.assert_not_awaited()

async def test_skips_when_describe_returns_none():
    # _download_bytes -> b"img"; describe_image_bytes -> None
    ok = await rescue_incoming_attachment(..., sleep=AsyncMock())
    assert ok is False
    save_message.assert_not_awaited()
    process_message.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd .worktrees/leitura-comprovante-chegada && uv run pytest tests/test_receipt_rescue.py -v`
Expected: FAIL (ImportError / módulo inexistente).

- [ ] **Step 3: Implementar `rescue_incoming_attachment` em `app/receipt_rescue.py`**

Ordem: checar `already_replayed(attachment_id)` e `has_trace(phone, created_at)`; se já há rastro, retornar False. Loop sobre `RESCUE_DOWNLOAD_DELAYS` (sugestão `(3, 8, 15, 25, 40, 60)`): `await sleep(delay)`, tentar `_download_bytes(data_url)` em try/except; no sucesso, sair do loop. Sem bytes ao fim: retornar False. Com bytes: re-checar `has_trace`/`has_payment_trace_until_now` (a Meta pode ter ingerido nesse meio-tempo); se traçado, retornar False. `canonico = _phone_variants(phone)[0] + "@s.whatsapp.net"`. `texto = await describe_image_bytes(img, phone=canonico)`; se None, retornar False. `log_event(REPLAY_EVENT, canonico, {attachment_id, conversation_id})` e `log_event(RESCUED_EVENT, canonico, {attachment_id, conversation_id, latency_s})`. `await save_message(canonico, "user", texto)`. `await process_message(canonico, texto)` (sem bypass). Retornar True. `_download_bytes(url)` usa `httpx.AsyncClient(timeout=60, follow_redirects=True)` e `raise_for_status()`.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd .worktrees/leitura-comprovante-chegada && uv run pytest tests/test_receipt_rescue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/receipt_rescue.py tests/test_receipt_rescue.py
git commit -m "feat(receipt): resgate de comprovante na chegada insistindo no download"
```

---

### Task 2: Disparo do resgate no webhook do Chatwoot

**Files:**
- Modify: `app/main.py` (bloco de anexos em `_handle_chatwoot_payload`, ~linha 1861-1891; novos helpers perto dele)
- Test: `tests/test_webhook.py`

**Interfaces:**
- Consumes: `rescue_incoming_attachment(...)` da Task 1.
- Produces: `_payload_inbox_id(payload: dict) -> int | None`; `_schedule_attachment_rescue(payload: dict, phone: str, conversation_id: int, attachments: list) -> None` (agenda `asyncio.create_task(rescue_incoming_attachment(...))`). Dedup em memória `_scheduled_rescue_ids` (OrderedDict com teto), análogo a `_synced_outgoing_ids`.

- [ ] **Step 1: Escrever os testes que falham** em `tests/test_webhook.py`

```python
# Patch app.main.rescue_incoming_attachment (AsyncMock) e capturar o agendamento.
# Payload Chatwoot message_created, incoming (message_type 0), inbox id = CHATWOOT_INBOX_ID,
# com 1 anexo imagem; _process_chatwoot_attachments mockado p/ devolver None.

async def test_schedules_rescue_when_arrival_read_fails():
    await _handle_chatwoot_payload(payload)
    rescue.assert_called_once()
    kwargs = rescue.call_args.kwargs
    assert kwargs["attachment_id"] == 2242 and kwargs["conversation_id"] == 542

async def test_no_rescue_for_other_inbox():
    # payload com inbox id != CHATWOOT_INBOX_ID
    await _handle_chatwoot_payload(payload)
    rescue.assert_not_called()

async def test_no_double_schedule_same_attachment():
    await _handle_chatwoot_payload(payload)   # message_created
    await _handle_chatwoot_payload(payload)   # entrega repetida, mesmo attachment_id
    assert rescue.call_count == 1

async def test_no_rescue_when_not_message_created():
    # payload event = "message_updated"
    await _handle_chatwoot_payload(payload)
    rescue.assert_not_called()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd .worktrees/leitura-comprovante-chegada && uv run pytest tests/test_webhook.py -k rescue -v`
Expected: FAIL.

- [ ] **Step 3: Implementar o disparo em `app/main.py`**

No bloco de anexos, no `else` em que `attachment_text` é falso (hoje faz `text = caption`), chamar `_schedule_attachment_rescue(payload, phone, conversation_id, attachments)` antes de cair na legenda. `_schedule_attachment_rescue`: retorna cedo se `payload.get("event") != "message_created"`; se `_payload_inbox_id(payload)` não casar com `_inbox_id()` de app.chatwoot; se não houver anexo imagem/arquivo com `data_url`. Pega o primeiro anexo imagem/arquivo, lê `attachment_id`, `data_url`, `created_at` (epoch; usar `created_at` da mensagem do payload). Dedup: se `attachment_id` já está em `_scheduled_rescue_ids`, não agenda; senão registra e `asyncio.create_task(rescue_incoming_attachment(phone=..., conversation_id=..., attachment_id=..., data_url=..., created_at=...))`. `_payload_inbox_id`: tenta `payload.get("inbox", {}).get("id")` e `payload.get("conversation", {}).get("inbox_id")`.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd .worktrees/leitura-comprovante-chegada && uv run pytest tests/test_webhook.py -k rescue -v`
Expected: PASS.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `cd .worktrees/leitura-comprovante-chegada && uv run pytest --tb=short`
Expected: PASS (nada quebrado no webhook).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(webhook): agenda resgate do comprovante quando a leitura na chegada falha"
```
