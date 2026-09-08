# eva-ativa executa nota-comando da atendente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ao adicionar a etiqueta `eva-ativa`, executar a nota-comando ("Eva, ...") que a atendente deixou pendente enquanto a Eva estava pausada, em vez de pular.

**Architecture:** Toda a decisão mora no ramo `_EVA_ACTIVE_LABEL in added` de `_apply_eva_label_action` (app/main.py). A nota é detectada pela última nota humana no Chatwoot (`get_last_patient_message`), classificada como comando por um regex (`^\s*eva\b`), e só executa se estiver *pendente* — suprimida e ainda não rodada, medido por eventos casados por conteúdo exato. A execução reusa `_handle_attendant_note` com um payload sintético.

**Tech Stack:** Python 3.14, FastAPI, pytest (async), Supabase (tabela `events`), Chatwoot API.

**Spec:** `docs/superpowers/specs/2026-09-08-eva-ativa-executa-nota-comando-design.md`

**Nota de ambiente:** todo comando roda dentro da worktree `.worktrees/eva-ativa-nota-comando`. Rode os testes com `uv run pytest --tb=short`.

---

### Task 1: `get_last_patient_message` devolve o texto da última nota

**Files:**
- Modify: `app/chatwoot.py` (função `get_last_patient_message`, ~199-239)
- Test: `tests/test_chatwoot.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione em `tests/test_chatwoot.py` (perto de `test_get_last_patient_message_reports_last_attendant_note_timestamp`):

```python
async def test_get_last_patient_message_returns_last_note_content():
    """Quem reativa a Eva pela label precisa do TEXTO da última nota da atendente para
    decidir se é uma nota-comando ("Eva, ...") e executá-la."""
    from app.chatwoot import get_last_patient_message

    messages = [
        {"message_type": 0, "content": "Presencial", "attachments": [], "created_at": 100},
        {"message_type": 1, "private": True, "sender": {"type": "user"},
         "content": "Eva, agende o paciente para 24/09 às 14:00", "created_at": 112},
    ]
    mock_client = _messages_get_mock(messages)

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict("os.environ", {
             "CHATWOOT_BASE_URL": "https://chat.example.com",
             "CHATWOOT_ACCOUNT_ID": "1",
             "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
         }):
        result = await get_last_patient_message(42)

    assert result["last_note_at"] == 112
    assert result["last_note_content"] == "Eva, agende o paciente para 24/09 às 14:00"


async def test_get_last_patient_message_note_content_is_none_without_notes():
    """Sem nota humana, last_note_content é None (não string vazia), espelhando last_note_at."""
    from app.chatwoot import get_last_patient_message

    messages = [
        {"message_type": 0, "content": "oi", "attachments": [], "created_at": 100},
    ]
    mock_client = _messages_get_mock(messages)
    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict("os.environ", {
             "CHATWOOT_BASE_URL": "https://chat.example.com",
             "CHATWOOT_ACCOUNT_ID": "1",
             "CHATWOOT_AGENT_BOT_TOKEN": "test-token",
         }):
        result = await get_last_patient_message(42)

    assert result["last_note_at"] is None
    assert result["last_note_content"] is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_chatwoot.py::test_get_last_patient_message_returns_last_note_content -q`
Expected: FAIL com `KeyError: 'last_note_content'`.

- [ ] **Step 3: Implementar o mínimo**

Em `app/chatwoot.py`, no final de `get_last_patient_message`, troque o cálculo de `notes`/return por (mantendo o resto igual):

```python
    notes = [
        m for m in messages
        if m.get("private") and (m.get("sender") or {}).get("type") == "user"
    ]
    last_note = max(notes, key=lambda m: m.get("created_at", 0)) if notes else None
    return {
        "content": (last.get("content") or "").strip(),
        "attachments": last.get("attachments") or [],
        "created_at": last.get("created_at") or 0,
        "last_note_at": (last_note.get("created_at") or 0) if last_note else None,
        "last_note_content": (last_note.get("content") or "").strip() if last_note else None,
    }
```

Atualize também o docstring da função para citar `last_note_content`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_chatwoot.py -q -k last_note`
Expected: PASS (inclui os testes existentes de `last_note_at`).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayexatavares/Projetos/psique-ai/.worktrees/eva-ativa-nota-comando
git add app/chatwoot.py tests/test_chatwoot.py
git commit -m "feat(chatwoot): get_last_patient_message devolve last_note_content"
```

---

### Task 2: helper `get_events_by_type` (leitura da tabela events)

**Files:**
- Modify: `app/database.py` (perto de `log_event`, ~296-306)
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione em `tests/test_webhook.py` (perto do bloco de testes de label, após `_LABEL_PHONE`):

```python
async def test_get_events_by_type_queries_stripped_phone():
    """get_events_by_type é o lado de leitura de log_event: filtra por phone (sem o
    sufixo @s.whatsapp.net, como é gravado) e por event_type, mais novos primeiro."""
    from app.database import get_events_by_type

    captured = {}

    class _Resp:
        data = [{"event_type": "x", "phone": "5581999", "metadata": {"content": "Eva, oi"}}]

    class _Q:
        def select(self, *a, **k): return self
        def eq(self, col, val):
            captured[col] = val
            return self
        def order(self, *a, **k): return self
        def limit(self, *a, **k): return self
        async def execute(self): return _Resp()

    class _Client:
        def from_(self, name):
            captured["table"] = name
            return _Q()

    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=_Client()):
        rows = await get_events_by_type("5581999@s.whatsapp.net", "attendant_note_suppressed_paused")

    assert captured["table"] == "events"
    assert captured["phone"] == "5581999"
    assert captured["event_type"] == "attendant_note_suppressed_paused"
    assert rows == [{"event_type": "x", "phone": "5581999", "metadata": {"content": "Eva, oi"}}]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_webhook.py::test_get_events_by_type_queries_stripped_phone -q`
Expected: FAIL com `ImportError: cannot import name 'get_events_by_type'`.

- [ ] **Step 3: Implementar o mínimo**

Em `app/database.py`, logo depois de `log_event`, adicione:

```python
async def get_events_by_type(phone: str, event_type: str, limit: int = 50) -> list[dict]:
    """Read side of log_event: recent events of one type for a phone, newest first.

    Phone is stripped the same way log_event strips it, so the JID form
    (…@s.whatsapp.net) and the bare-digits form both match the stored rows. Returns
    [] on any error — leitura best-effort, nunca quebra o fluxo principal."""
    try:
        client = await get_supabase()
        resp = (
            await client.from_("events")
            .select("event_type, phone, metadata, created_at")
            .eq("phone", _strip_phone(phone))
            .eq("event_type", event_type)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []
```

`_strip_phone` já está importado em `app/database.py` (usado por `log_event`).

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_webhook.py::test_get_events_by_type_queries_stripped_phone -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_webhook.py
git commit -m "feat(database): get_events_by_type para ler eventos por tipo/telefone"
```

---

### Task 3: helper puro `_looks_like_eva_command`

**Files:**
- Modify: `app/main.py` (imports no topo + novo helper perto de `_apply_eva_label_action`)
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione em `tests/test_webhook.py`:

```python
def test_looks_like_eva_command():
    from app.main import _looks_like_eva_command

    assert _looks_like_eva_command("Eva, agende para 24/09") is True
    assert _looks_like_eva_command("eva: agenda amanhã") is True
    assert _looks_like_eva_command("Eva agende o paciente") is True
    assert _looks_like_eva_command("  Eva, com espaços") is True
    assert _looks_like_eva_command("Evaristo ligou reclamando") is False
    assert _looks_like_eva_command("Evangelina confirmou") is False
    assert _looks_like_eva_command("paciente pediu retorno") is False
    assert _looks_like_eva_command("") is False
    assert _looks_like_eva_command(None) is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_webhook.py::test_looks_like_eva_command -q`
Expected: FAIL com `ImportError: cannot import name '_looks_like_eva_command'`.

- [ ] **Step 3: Implementar o mínimo**

Em `app/main.py`, adicione `import re` no bloco de imports do topo (após `import os`). Depois, logo antes de `_apply_eva_label_action`, adicione:

```python
_EVA_COMMAND_RE = re.compile(r"^\s*eva\b", re.IGNORECASE)


def _looks_like_eva_command(text: str | None) -> bool:
    """A nota é um comando para a Eva quando começa chamando-a pelo nome: "Eva, ...",
    "Eva ...", "eva: ...". "Evaristo"/"Evangelina" não contam — o \\b exige um limite de
    palavra logo depois de "eva"."""
    return bool(text and _EVA_COMMAND_RE.match(text))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_webhook.py::test_looks_like_eva_command -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(main): _looks_like_eva_command para reconhecer nota-comando"
```

---

### Task 4: helper `_note_command_pending` (trava anti-dupla)

**Files:**
- Modify: `app/main.py` (novo helper perto de `_looks_like_eva_command`)
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione em `tests/test_webhook.py`:

```python
async def test_note_command_pending_true_when_suppressed_and_not_executed():
    from app.main import _note_command_pending

    async def fake_events(phone, event_type, limit=50):
        if event_type == "attendant_note_suppressed_paused":
            return [{"metadata": {"content": "Eva, agende 24/09"}}]
        return []  # nada em attendant_note_resumed_executed

    with patch("app.database.get_events_by_type", side_effect=fake_events):
        assert await _note_command_pending("5581999@s.whatsapp.net", "Eva, agende 24/09") is True


async def test_note_command_pending_false_when_already_executed():
    from app.main import _note_command_pending

    async def fake_events(phone, event_type, limit=50):
        return [{"metadata": {"content": "Eva, agende 24/09"}}]  # aparece nos dois tipos

    with patch("app.database.get_events_by_type", side_effect=fake_events):
        assert await _note_command_pending("5581999@s.whatsapp.net", "Eva, agende 24/09") is False


async def test_note_command_pending_false_when_never_suppressed():
    """Nota executada ao vivo (Eva ativa) grava attendant_note_received, nunca
    suppressed_paused → nunca é considerada pendente. Protege o PIX em dobro."""
    from app.main import _note_command_pending

    async def fake_events(phone, event_type, limit=50):
        return []

    with patch("app.database.get_events_by_type", side_effect=fake_events):
        assert await _note_command_pending("5581999@s.whatsapp.net", "Eva, agende 24/09") is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_webhook.py -q -k note_command_pending`
Expected: FAIL com `ImportError: cannot import name '_note_command_pending'`.

- [ ] **Step 3: Implementar o mínimo**

Em `app/main.py`, logo após `_looks_like_eva_command`, adicione:

```python
async def _note_command_pending(phone: str, note_text: str) -> bool:
    """True se esta nota exata foi suprimida enquanto a Eva estava pausada e ainda NÃO
    rodou. Casa por conteúdo exato (webhook e API de mensagens do Chatwoot podem formatar
    timestamps diferente; o texto é estável). Uma nota executada ao vivo grava
    attendant_note_received — nunca attendant_note_suppressed_paused — logo nunca é
    pendente, o que preserva a proteção contra confirmação/PIX em dobro (caso
    5581979037093)."""
    from app.database import get_events_by_type
    suppressed = await get_events_by_type(phone, "attendant_note_suppressed_paused")
    if not any((e.get("metadata") or {}).get("content") == note_text for e in suppressed):
        return False
    executed = await get_events_by_type(phone, "attendant_note_resumed_executed")
    return not any((e.get("metadata") or {}).get("content") == note_text for e in executed)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_webhook.py -q -k note_command_pending`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(main): _note_command_pending (suprimida e não executada)"
```

---

### Task 5: gravar o conteúdo completo da nota no evento de supressão

**Files:**
- Modify: `app/main.py` (`_handle_attendant_note`, evento `attendant_note_suppressed_paused`, ~1244-1246)
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Escrever o teste que falha**

`_note_command_pending` casa por conteúdo exato, então o evento de supressão precisa
guardar o texto INTEIRO (hoje corta em 300). Adicione em `tests/test_webhook.py`:

```python
async def test_suppressed_note_event_stores_full_content():
    """O evento de supressão guarda o texto completo (sem corte de 300 chars), porque a
    trava de pendência casa por conteúdo exato."""
    from app.main import _handle_attendant_note

    long_note = "Eva, " + ("agende " * 100)  # > 300 chars
    payload = {
        "message_type": 1, "private": True, "content": long_note,
        "sender": {"type": "user"},
        "conversation": {"id": 7, "meta": {"sender": {"phone_number": "5581999"}}},
    }
    logged = {}
    with patch("app.main._eva_paused_for_phone", new_callable=AsyncMock, return_value=True), \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        await _handle_attendant_note(payload)

    calls = {c.args[0]: c.args[2] for c in mock_log.await_args_list}
    assert "attendant_note_suppressed_paused" in calls
    assert calls["attendant_note_suppressed_paused"]["content"] == long_note
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_webhook.py::test_suppressed_note_event_stores_full_content -q`
Expected: FAIL — o conteúdo vem cortado em 300 chars (`content[:300]`).

- [ ] **Step 3: Implementar o mínimo**

Em `app/main.py`, no ramo de supressão de `_handle_attendant_note`, troque:

```python
        await log_event("attendant_note_suppressed_paused", phone, {
            "content": content[:300], "conversation_id": conv_id,
        })
```

por (conteúdo completo — a trava de pendência casa por texto exato):

```python
        await log_event("attendant_note_suppressed_paused", phone, {
            "content": content, "conversation_id": conv_id,
        })
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_webhook.py::test_suppressed_note_event_stores_full_content -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(main): evento de supressão guarda o texto completo da nota"
```

---

### Task 6: decisão na reativação eva-ativa + execução da nota-comando

**Files:**
- Modify: `app/main.py` (`_execute_attendant_note_on_resume` novo + ramo `_EVA_ACTIVE_LABEL in added` de `_apply_eva_label_action`, ~1007-1063)
- Test: `tests/test_webhook.py` (novos + atualização de `test_conv_updated_eva_ativa_skips_replay_when_note_is_newer`)

- [ ] **Step 1: Escrever os testes que falham**

Adicione em `tests/test_webhook.py` (perto dos outros testes `test_conv_updated_eva_ativa_*`):

```python
async def test_conv_updated_eva_ativa_executes_pending_command_note():
    """Nota-comando é a última coisa e está pendente → a Eva executa (via
    _handle_attendant_note) e NÃO reproduz a mensagem do paciente."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=5001)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._note_command_pending", new_callable=AsyncMock, return_value=True), \
         patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "obrigada!", "attachments": [], "created_at": 100,
            "last_note_at": 130, "last_note_content": "Eva, agende 24/09 às 14h presencial",
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_push.assert_not_awaited()
    mock_note.assert_awaited_once()
    sent = mock_note.await_args[0][0]
    assert sent["content"] == "Eva, agende 24/09 às 14h presencial"
    assert sent["conversation"]["id"] == 5001
    assert sent["sender"]["type"] == "user"
    assert any(c.args[0] == "attendant_note_resumed_executed" for c in mock_log.await_args_list)


async def test_conv_updated_eva_ativa_command_note_but_patient_spoke_after():
    """Paciente falou DEPOIS da nota-comando → a mensagem do paciente vence: reproduz o
    paciente e NÃO executa o comando."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=5002)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._note_command_pending", new_callable=AsyncMock, return_value=True), \
         patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "na verdade prefiro online", "attachments": [], "created_at": 200,
            "last_note_at": 130, "last_note_content": "Eva, agende 24/09 às 14h presencial",
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_note.assert_not_awaited()
    assert mock_push.await_args[0][1] == "na verdade prefiro online"


async def test_conv_updated_eva_ativa_internal_note_not_a_command():
    """Última nota não começa com "Eva" (recado interno) → não executa nem reproduz."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=5003)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._note_command_pending", new_callable=AsyncMock, return_value=True) as mock_pending, \
         patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "ok", "attachments": [], "created_at": 100,
            "last_note_at": 130, "last_note_content": "paciente ligou reclamando",
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_note.assert_not_awaited()
    mock_push.assert_not_awaited()
    mock_pending.assert_not_awaited()  # nem chega a checar pendência


async def test_conv_updated_eva_ativa_command_note_not_pending_is_skipped():
    """Nota-comando é a última mas NÃO está pendente (já rodou / rodou ao vivo) → não
    executa de novo e não reproduz o paciente. Regressão do PIX em dobro (5581979037093)."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=5004)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._note_command_pending", new_callable=AsyncMock, return_value=False), \
         patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "Presencial", "attachments": [], "created_at": 100,
            "last_note_at": 130, "last_note_content": "Eva, agende 24/09 às 14h",
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_note.assert_not_awaited()
    mock_push.assert_not_awaited()
```

Além disso, **substitua** o corpo do teste existente
`test_conv_updated_eva_ativa_skips_replay_when_note_is_newer` para refletir a nova regra
(uma nota que não é comando não é reproduzida nem executada — mantém a proteção do PIX):

```python
async def test_conv_updated_eva_ativa_skips_replay_when_note_is_newer():
    """A atendente respondeu o paciente na mão e deixou uma nota (recado interno) mais
    nova que a mensagem do paciente. Reprocessar a mensagem antiga mandaria a confirmação
    e o PIX duas vezes (caso 5581979037093). A nota não começa com "Eva", então nada roda
    e nada é reproduzido."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4201)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume, \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "Presencial", "attachments": [], "created_at": 100,
            "last_note_at": 112, "last_note_content": "respondi por telefone, tudo certo",
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_resume.assert_awaited_once_with(_LABEL_PHONE_JID)
    mock_note.assert_not_awaited()
    mock_push.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_webhook.py -q -k "eva_ativa"`
Expected: FAIL — `_execute_attendant_note_on_resume`/nova lógica ainda não existe; a nota-comando não é executada.

- [ ] **Step 3: Implementar**

Em `app/main.py`, adicione o helper de execução logo antes de `_apply_eva_label_action`:

```python
async def _execute_attendant_note_on_resume(phone: str, conversation_id, note_text: str) -> None:
    """Executa uma nota-comando que ficou pendente (suprimida enquanto a Eva estava
    pausada) quando a atendente reativa pela label eva-ativa. Reusa o caminho normal
    _handle_attendant_note com um payload sintético e grava o marcador de execução, para
    não repetir numa próxima reativação."""
    from app.phone import _strip_phone
    bare = _strip_phone(phone)
    synthetic = {
        "message_type": 1,
        "private": True,
        "content": note_text,
        "sender": {"type": "user"},
        "conversation": {"id": conversation_id, "meta": {"sender": {"phone_number": bare}}},
    }
    await _handle_attendant_note(synthetic)
    await log_event("attendant_note_resumed_executed", phone, {
        "content": note_text, "conversation_id": conversation_id,
    })
```

Depois, no ramo `elif _EVA_ACTIVE_LABEL in added:` de `_apply_eva_label_action`, troque o
bloco que hoje faz o skip (as linhas do `if _note_at and _note_at >= (last.get("created_at") or 0):`
que setam `last = None`) por:

```python
                _note_at = last.get("last_note_at") if last else None
                if last and _note_at and _note_at >= (last.get("created_at") or 0):
                    # A última coisa da conversa é uma nota da atendente, mais nova que a
                    # mensagem do paciente. Se for uma nota-comando ("Eva, ...") ainda
                    # pendente, executa; senão mantém o skip antigo — sem reprocessar a
                    # mensagem do paciente, pra não mandar confirmação/PIX em dobro
                    # (caso 5581979037093).
                    note_text = last.get("last_note_content") or ""
                    if _looks_like_eva_command(note_text) and await _note_command_pending(phone, note_text):
                        logger.info(
                            "EVA_ATIVA_NOTE_COMMAND phone=%s conv=%s: %.60s",
                            phone, conversation_id, note_text,
                        )
                        await _execute_attendant_note_on_resume(phone, conversation_id, note_text)
                    else:
                        logger.info(
                            "EVA_ATIVA_REPLAY_SKIPPED phone=%s conv=%s — nota da atendente "
                            "posterior à mensagem do paciente: %.60s",
                            phone, conversation_id, last.get("content"),
                        )
                        await log_event("eva_ativa_replay_skipped", phone, {
                            "content": (last.get("content") or "")[:300],
                            "conversation_id": conversation_id,
                        })
                    last = None
```

O bloco `if last:` seguinte (replay da mensagem do paciente) permanece **inalterado**.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_webhook.py -q -k "eva_ativa"`
Expected: PASS (novos + os existentes `..._added_resumes_and_reprocesses`, `..._replays_when_note_is_older`, `..._skipped_replay_does_not_read_attachments`, `..._added_reprocesses_attachment_only_receipt`, `..._saves_receipt_when_not_yet_persisted`).

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(main): eva-ativa executa nota-comando pendente na reativação"
```

---

### Task 7: suíte completa + verificação final

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS (sem regressões). Se algum teste antigo de label quebrar, confirmar que é por causa da nova regra e ajustar conforme a spec — nunca afrouxar a proteção do PIX em dobro.

- [ ] **Step 2: Revisar o diff contra a spec**

Confirme os 5 pontos da "Regra de decisão" da spec: comando pendente executa; comando não-pendente é pulado; paciente mais novo vence; nota não-Eva não faz nada; remoção da eva-inativa intacta.

- [ ] **Step 3: Commit final se necessário**

```bash
git add -A
git commit -m "test(webhook): suíte verde para eva-ativa executa nota-comando" || echo "nada a commitar"
```

---

## Self-Review (autor do plano)

- **Cobertura da spec:** Task 1 (`last_note_content`), Task 2 (`get_events_by_type`), Task 3 (`_looks_like_eva_command` + regra "começa com Eva"), Task 4 (trava de pendência), Task 5 (conteúdo completo no evento de supressão), Task 6 (regra de decisão "o último vence" + execução + atualização do teste de regressão), Task 7 (suíte). Todos os itens da spec têm task.
- **Sem placeholders:** todo passo traz código/comando concreto.
- **Consistência de nomes:** `get_last_patient_message`→`last_note_content`; `get_events_by_type(phone, event_type)`; `_looks_like_eva_command(text)`; `_note_command_pending(phone, note_text)`; `_execute_attendant_note_on_resume(phone, conversation_id, note_text)`; eventos `attendant_note_suppressed_paused` e `attendant_note_resumed_executed` casados por `metadata.content` — usados igual em todas as tasks.
