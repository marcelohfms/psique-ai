# Varredura de mensagem perdida — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recuperar sozinho a última mensagem do paciente que ficou sem resposta por perda do turno no buffer em memória, sem depender da nota manual.

**Architecture:** Módulo novo `app/lost_message_recovery.py` com uma função pura de seleção de candidato e uma `sweep` que reprocessa via `process_message`. Uma tarefa de fundo iniciada no `lifespan` chama `sweep` a cada 2 min. Reusa as travas existentes: portão de pausa e dedup de checkpoint dentro do `process_message`.

**Tech Stack:** Python, asyncio, FastAPI lifespan, Supabase (tabelas `messages`/`events`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-varredura-mensagem-perdida-design.md`

## Global Constraints

- Rodar DENTRO do processo do app (in-process). Nunca subprocesso: `process_message` depende de `graph_module.chatbot` com checkpointer do Postgres (`app/main.py:272`) e dos locks em memória; o default `build_graph()` (`app/graph/graph.py:120`) não tem o checkpointer real.
- Reprocessar com `process_message(full_phone, text)` SEM `bypass_pause` e SEM `save_message` (mensagem já gravada no webhook, `app/main.py:1061`).
- `messages.phone` é bare (ex.: `5581988197076`); `process_message`/eventos usam o jid cheio `f"{phone}@s.whatsapp.net"`, como em `_recover_messages_lost_to_restart`.
- Defaults das env vars: `LOST_MSG_SWEEP_FLOOR_MIN=5`, `LOST_MSG_SWEEP_INTERVAL_MIN=2`, `LOST_MSG_SWEEP_WINDOW_MIN=360`.
- Testes seguem a estrutura de `tests/`; módulo novo ⇒ arquivo novo `tests/test_lost_message_recovery.py`.

## Review Focus

- Mensagem já respondida mas lenta (retry de rate limit em voo): o reprocesso não pode gerar segunda resposta. Coberto pelo dedup de checkpoint do `process_message`; o piso de 5 min reduz a chance. Teste no Task 2 (dedup ⇒ no-op).
- Conversa transferida/pausada cuja última linha é do paciente: não pode fazer a Eva falar. Coberto pelo portão de pausa. Teste no Task 2 (pausa ⇒ no-op).
- Última linha do telefone é `attendant` (nota) ou `assistant`: não é candidato. Teste no Task 1.
- Um candidato que estoura (`process_message` lança) não pode derrubar a rodada nem impedir os demais. Teste no Task 2.
- Mesma mensagem varrida em dois ticks seguidos antes da resposta cair: não reempurrar duas vezes. Guard por evento `lost_message_replayed`. Teste no Task 2.

---

### Task 1: Seleção de candidato (função pura)

**Files:**
- Create: `app/lost_message_recovery.py`
- Test: `tests/test_lost_message_recovery.py`

**Interfaces:**
- Produces: `pick_candidates(rows: list[dict], now: datetime, floor_min: int) -> list[dict]`. `rows` são linhas de `messages` com chaves `phone` (bare), `role`, `content`, `created_at` (ISO str), em qualquer ordem. Retorna, por telefone, a última linha SE `role == "user"`, `content` não-vazio, e `created_at <= now - floor_min`; cada item retornado é `{"phone", "content", "created_at"}`.

- [ ] **Step 1: Write the failing test**

```python
def test_pick_candidates_user_last_and_old_enough():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [
        {"phone": "551100000001", "role": "user", "content": "sim",
         "created_at": "2026-11-19T14:50:00+00:00"},   # 10 min atrás → candidato
    ]
    out = pick_candidates(rows, now, floor_min=5)
    assert out == [{"phone": "551100000001", "content": "sim",
                    "created_at": "2026-11-19T14:50:00+00:00"}]

def test_pick_candidates_excludes_recent_within_floor():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [{"phone": "551100000001", "role": "user", "content": "sim",
             "created_at": "2026-11-19T14:58:00+00:00"}]  # 2 min → ainda no piso
    assert pick_candidates(rows, now, floor_min=5) == []

def test_pick_candidates_excludes_when_last_is_assistant_or_attendant():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    base = "2026-11-19T14:50:00+00:00"
    later = "2026-11-19T14:51:00+00:00"
    for last_role in ("assistant", "attendant"):
        rows = [
            {"phone": "551100000001", "role": "user", "content": "sim", "created_at": base},
            {"phone": "551100000001", "role": last_role, "content": "ok", "created_at": later},
        ]
        assert pick_candidates(rows, now, floor_min=5) == []

def test_pick_candidates_skips_empty_content():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [{"phone": "551100000001", "role": "user", "content": "",
             "created_at": "2026-11-19T14:50:00+00:00"}]
    assert pick_candidates(rows, now, floor_min=5) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lost_message_recovery.py -v`
Expected: FAIL (ImportError / `pick_candidates` não definida)

- [ ] **Step 3: Implement `pick_candidates(rows, now, floor_min) -> list[dict]` em `app/lost_message_recovery.py`**

Reduzir a última linha por telefone ordenando por `created_at` (mesma ideia do `_recover_messages_lost_to_restart`). Parse de `created_at` com `datetime.fromisoformat`. Cutoff = `now - timedelta(minutes=floor_min)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lost_message_recovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lost_message_recovery.py tests/test_lost_message_recovery.py
git commit -m "feat(recovery): seleção de candidato para mensagem perdida no buffer"
```

---

### Task 2: `sweep` — reprocessa candidatos com idempotência e isolamento

**Files:**
- Modify: `app/lost_message_recovery.py`
- Test: `tests/test_lost_message_recovery.py`

**Interfaces:**
- Consumes: `pick_candidates` (Task 1).
- Produces: `async def sweep(now, *, floor_min=LOST_MSG_SWEEP_FLOOR_MIN, window_min=LOST_MSG_SWEEP_WINDOW_MIN) -> int`. Busca linhas de `messages` em `[now - window_min, now]`, chama `pick_candidates`, e para cada candidato ainda não reprocessado chama `process_message(f"{phone}@s.whatsapp.net", content)` e `log_event("lost_message_replayed", full_phone, {...})`. Retorna quantos reprocessou.
- Helper `async def _already_replayed(full_phone: str, message_created_at: str) -> bool`: True se existe evento `lost_message_replayed` para `full_phone` com `metadata.message_created_at == message_created_at`.

- [ ] **Step 1: Write the failing tests** (mockando `get_supabase`, `process_message`, `log_event` via monkeypatch no módulo)

```python
# caso perdido → reprocessa uma vez e loga o evento
async def test_sweep_reprocesses_lost_message(monkeypatch): ...
#   process_message chamado com ("551...@s.whatsapp.net", "sim"), sem bypass_pause;
#   log_event chamado com "lost_message_replayed"; retorno == 1

# idempotência → já tem evento lost_message_replayed p/ essa created_at ⇒ não reprocessa
async def test_sweep_skips_already_replayed(monkeypatch): ...
#   process_message NÃO chamado; retorno == 0

# candidato que estoura não derruba a rodada nem bloqueia os demais
async def test_sweep_isolates_failing_candidate(monkeypatch): ...
#   dois candidatos, process_message lança no 1º; 2º ainda é chamado; retorno == 1
```

(Pausa e dedup não precisam de teste próprio aqui: são comportamento do
`process_message`, já coberto em `tests/test_process_message.py`. `sweep` só
garante que chama `process_message` SEM `bypass_pause`, verificado no 1º teste.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lost_message_recovery.py -v`
Expected: FAIL (`sweep` não definida)

- [ ] **Step 3: Implement `sweep` e `_already_replayed` em `app/lost_message_recovery.py`**

Import local de `process_message`/`save_message` dentro da função (evita ciclo com `app.main`, como faz `receipt_reconciliation.replay_dropped_receipt`). `log_event` de `app.database`. Query de `messages`: `select("phone, role, content, created_at").gte("created_at", lower).lte("created_at", upper).order("created_at")`. Try/except por candidato com `logger.exception("LOST_MSG_SWEEP_CANDIDATE_FAILED ...")`. Ler env vars no topo do módulo com defaults da Global Constraints.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lost_message_recovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/lost_message_recovery.py tests/test_lost_message_recovery.py
git commit -m "feat(recovery): sweep reprocessa mensagem perdida respeitando pausa e dedup"
```

---

### Task 3: Fio no lifespan (tarefa de fundo) + gate de ambiente

**Files:**
- Modify: `app/lost_message_recovery.py` (loop)
- Modify: `app/main.py` (start/stop no `lifespan`)
- Test: `tests/test_lost_message_recovery.py`

**Interfaces:**
- Consumes: `sweep` (Task 2).
- Produces: `async def sweep_loop(interval_min: float = LOST_MSG_SWEEP_INTERVAL_MIN, *, _sleep=asyncio.sleep) -> None`: laço que chama `sweep(datetime.now(timezone.utc))` e dorme `interval_min*60`; nunca deixa exceção do `sweep` vazar (try/except + log). `start_lost_message_sweep() -> asyncio.Task | None` e `stop_lost_message_sweep(task)` para o `lifespan`; `start` retorna `None` quando `SCHEDULER_LIVE` é falso (mesmo gate do agendador, `app/scheduler.py`).

- [ ] **Step 1: Write the failing test**

```python
# uma volta do loop chama sweep e uma exceção do sweep não quebra o loop
async def test_sweep_loop_runs_and_swallows_errors(monkeypatch):
    calls = []
    async def fake_sweep(now): calls.append(now); raise RuntimeError("boom")
    monkeypatch.setattr(mod, "sweep", fake_sweep)
    # _sleep que cancela depois da 1ª iteração via asyncio.CancelledError
    ...
    assert len(calls) >= 1   # rodou e não propagou o RuntimeError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lost_message_recovery.py -v`
Expected: FAIL (`sweep_loop` não definida)

- [ ] **Step 3: Implement `sweep_loop`, `start_lost_message_sweep`, `stop_lost_message_sweep`; ligar no `lifespan`**

No `app/main.py` `lifespan`, junto do `start_scheduler`: `task = start_lost_message_sweep()`; no teardown (perto do `stop_scheduler`/`_drain_buffer_on_shutdown`): `await stop_lost_message_sweep(task)` (cancela e aguarda, tolerando `CancelledError`). `SCHEDULER_LIVE` importado de `app.scheduler`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lost_message_recovery.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest --tb=short`
Expected: PASS (nada quebrado em `test_process_message`, `test_buffer`, etc.)

- [ ] **Step 6: Commit**

```bash
git add app/lost_message_recovery.py app/main.py tests/test_lost_message_recovery.py
git commit -m "feat(recovery): tarefa de fundo varre mensagens perdidas a cada 2 min"
```
