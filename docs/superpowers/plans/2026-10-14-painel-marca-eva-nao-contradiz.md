# Painel marca a consulta, a Eva não contradiz — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que a Eva reofertе horários ou negue vaga depois que a atendente marcou a consulta pelo painel no meio da conversa.

**Architecture:** Dois pontos no `patient_agent_node`, ambos acionados pelo mesmo gatilho (`_recent_panel_booking`): (1) uma diretiva injetada no system prompt (prevenção) e (2) um guard pós-resposta `GUARD_PANEL_BOOKED` que troca a resposta contraditória por uma confirmação (rede de segurança). O gatilho infere a marcação recente pelo evento `appointment_booked` com `origem == "painel"` e se desliga sozinho quando a Eva faz uma busca nova (`slots_offered` mais recente).

**Tech Stack:** Python 3.14, LangGraph, pytest (async), uv.

**Spec:** `docs/superpowers/specs/2026-10-14-painel-marca-eva-nao-contradiz-design.md`

---

## File Structure

- **Modify `app/graph/nodes.py`:**
  - Nova constante `PANEL_BOOKING_GUARD_MAX_AGE` (junto de `STALE_SLOT_OFFER_MAX_AGE`, ~linha 385).
  - Novo helper `_recent_panel_booking(phone, now, upcoming)` (junto de `_last_slot_offer_is_stale`, ~linha 418).
  - Novo detector `_denies_availability(text)` (junto de `_looks_like_slot_offer`, ~linha 403).
  - Injeção da diretiva no `patient_agent_node`, logo após o bloco de consultas agendadas (após a linha 2930), guardando o resultado em `_panel_booking`.
  - Guard `GUARD_PANEL_BOOKED` imediatamente ANTES do `GUARD_STALE_SLOTS` (~linha 3399), reutilizando `_panel_booking`.
- **Modify `tests/test_process_message.py`:** testes de unidade do helper e do detector, e testes de integração mockados do prompt e do guard. Adicionar ao FIM do arquivo (convenção do projeto para evitar conflito — ver memória `stacked-prs-conflito-testes-append`).

---

## Task 1: Gatilho `_recent_panel_booking` + constante

**Files:**
- Modify: `app/graph/nodes.py` (~linha 385 e ~linha 418)
- Test: `tests/test_process_message.py` (append ao fim)

- [ ] **Step 1: Write the failing unit tests**

Adicionar ao FIM de `tests/test_process_message.py`:

```python
# ── _recent_panel_booking: trava painel-marcou ───────────────────────────────
# Caso Dario (5581998196655, 14/10/2026): a clínica marcou pelo painel e a Eva,
# com ofertas antigas no histórico, reofertou e negou a vaga recém-confirmada.

def _panel_events(booked_minutes_ago=5, offered_minutes_ago=None, origem="painel",
                  appointment_id="evt-painel"):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    booked = [{
        "event_type": "appointment_booked",
        "metadata": {"origem": origem, "appointment_id": appointment_id},
        "created_at": (now - timedelta(minutes=booked_minutes_ago)).isoformat(),
    }]
    offers = []
    if offered_minutes_ago is not None:
        offers = [{
            "event_type": "slots_offered",
            "created_at": (now - timedelta(minutes=offered_minutes_ago)).isoformat(),
        }]
    return booked, offers


def _active_upcoming(appointment_id="evt-painel"):
    return [{
        "appointment_id": appointment_id,
        "status": "scheduled",
        "start_time": "2026-10-14T17:00:00+00:00",
        "already_occurred": False,
        "canceled_unpaid": False,
    }]


def _patch_panel_events(booked, offers):
    async def fake_events(phone, event_type, limit=50):
        return {"appointment_booked": booked, "slots_offered": offers}.get(event_type, [])
    return patch("app.graph.nodes.get_events_by_type", side_effect=fake_events)


async def test_recent_panel_booking_matches_fresh_panel_booking():
    from datetime import datetime, timezone
    from app.graph.nodes import _recent_panel_booking
    booked, offers = _panel_events(booked_minutes_ago=5, offered_minutes_ago=30)
    with _patch_panel_events(booked, offers):
        result = await _recent_panel_booking(
            PHONE, datetime.now(timezone.utc), _active_upcoming(),
        )
    assert result is not None
    assert result["appointment_id"] == "evt-painel"
    assert result["when"] == "14/10 às 14:00"


async def test_recent_panel_booking_off_when_offer_is_newer():
    from datetime import datetime, timezone
    from app.graph.nodes import _recent_panel_booking
    # oferta de 2min atrás é mais nova que a marcação de 5min → trava desliga
    booked, offers = _panel_events(booked_minutes_ago=5, offered_minutes_ago=2)
    with _patch_panel_events(booked, offers):
        result = await _recent_panel_booking(
            PHONE, datetime.now(timezone.utc), _active_upcoming(),
        )
    assert result is None


async def test_recent_panel_booking_off_when_booking_too_old():
    from datetime import datetime, timezone
    from app.graph.nodes import _recent_panel_booking
    booked, offers = _panel_events(booked_minutes_ago=7 * 60, offered_minutes_ago=None)
    with _patch_panel_events(booked, offers):
        result = await _recent_panel_booking(
            PHONE, datetime.now(timezone.utc), _active_upcoming(),
        )
    assert result is None


async def test_recent_panel_booking_off_when_appointment_not_active():
    from datetime import datetime, timezone
    from app.graph.nodes import _recent_panel_booking
    booked, offers = _panel_events(booked_minutes_ago=5)
    canceled = [{**_active_upcoming()[0], "status": "scheduled", "canceled_unpaid": True}]
    with _patch_panel_events(booked, offers):
        result = await _recent_panel_booking(
            PHONE, datetime.now(timezone.utc), canceled,
        )
    assert result is None


async def test_recent_panel_booking_off_when_no_panel_event():
    from datetime import datetime, timezone
    from app.graph.nodes import _recent_panel_booking
    booked, offers = _panel_events(booked_minutes_ago=5, origem="eva")  # não é painel
    with _patch_panel_events(booked, offers):
        result = await _recent_panel_booking(
            PHONE, datetime.now(timezone.utc), _active_upcoming(),
        )
    assert result is None


async def test_recent_panel_booking_none_on_read_error():
    from datetime import datetime, timezone
    from app.graph.nodes import _recent_panel_booking
    with patch("app.graph.nodes.get_events_by_type",
               side_effect=RuntimeError("db down")):
        result = await _recent_panel_booking(
            PHONE, datetime.now(timezone.utc), _active_upcoming(),
        )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k recent_panel_booking -q`
Expected: FAIL com `ImportError`/`AttributeError: _recent_panel_booking`.

- [ ] **Step 3: Add the constant**

Em `app/graph/nodes.py`, logo após a linha 385 (`STALE_SLOT_OFFER_MAX_AGE = timedelta(hours=2)`), acrescentar:

```python
# Janela em que a trava painel-marcou ainda pode disparar (cobre uma conversa
# inteira com vai e volta de pagamento). Ver _recent_panel_booking.
PANEL_BOOKING_GUARD_MAX_AGE = timedelta(hours=6)
```

- [ ] **Step 4: Implement the helper**

Em `app/graph/nodes.py`, logo após o fim de `_last_slot_offer_is_stale` (~linha 418), acrescentar:

```python
async def _recent_panel_booking(phone: str, now: datetime, upcoming: list[dict]) -> dict | None:
    """Consulta marcada pelo painel há pouco e ainda ativa, ou None.

    Liga a trava que impede a Eva de contradizer (reofertar horários / negar vaga)
    uma consulta que a clínica acabou de marcar pelo painel no meio da conversa
    (caso Dario, 5581998196655, 14/10/2026). Todas as condições são obrigatórias:
      1. existe appointment_booked com metadata.origem == 'painel' há menos de
         PANEL_BOOKING_GUARD_MAX_AGE;
      2. essa marcação é mais recente que o último slots_offered (ou não há) — é o
         que desliga a trava sozinha quando a Eva faz uma busca nova e legítima;
      3. a consulta apontada ainda está ativa (aparece em `upcoming` como scheduled,
         não cancelada nem já realizada).
    Devolve {'appointment_id', 'when'} (when = 'DD/MM às HH:MM' em Recife) ou None.
    Qualquer erro de leitura devolve None (best-effort, nunca trava a conversa)."""
    try:
        booked_events = await get_events_by_type(phone, "appointment_booked", limit=10)
    except Exception:
        return None
    panel = next(
        (e for e in booked_events if (e.get("metadata") or {}).get("origem") == "painel"),
        None,
    )
    if not panel:
        return None
    try:
        booked_at = datetime.fromisoformat(panel["created_at"])
    except (KeyError, ValueError, TypeError):
        return None
    if booked_at.tzinfo is None:
        booked_at = booked_at.replace(tzinfo=timezone.utc)
    if now - booked_at > PANEL_BOOKING_GUARD_MAX_AGE:
        return None

    try:
        offers = await get_events_by_type(phone, "slots_offered", limit=1)
    except Exception:
        offers = []
    if offers:
        try:
            offered_at = datetime.fromisoformat(offers[0]["created_at"])
            if offered_at.tzinfo is None:
                offered_at = offered_at.replace(tzinfo=timezone.utc)
            if offered_at >= booked_at:
                return None  # oferta mais nova que a marcação → trava desliga
        except (KeyError, ValueError, TypeError):
            pass

    appt_id = (panel.get("metadata") or {}).get("appointment_id")
    if not appt_id:
        return None
    match = next(
        (a for a in (upcoming or [])
         if a.get("appointment_id") == appt_id
         and a.get("status") == "scheduled"
         and not a.get("already_occurred")
         and not a.get("canceled_unpaid")),
        None,
    )
    if not match:
        return None
    from zoneinfo import ZoneInfo as _ZI
    try:
        dt = datetime.fromisoformat(match["start_time"]).astimezone(_ZI("America/Recife"))
    except (KeyError, ValueError, TypeError):
        return None
    return {"appointment_id": appt_id, "when": dt.strftime("%d/%m às %H:%M")}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k recent_panel_booking -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(agendamento): detecta marcação recente pelo painel (_recent_panel_booking)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Detector `_denies_availability`

**Files:**
- Modify: `app/graph/nodes.py` (~linha 403, após `_looks_like_slot_offer`)
- Test: `tests/test_process_message.py` (append ao fim)

- [ ] **Step 1: Write the failing unit test**

Adicionar ao FIM de `tests/test_process_message.py`:

```python
def test_denies_availability():
    from app.graph.nodes import _denies_availability
    assert _denies_availability("Para hoje não temos horários disponíveis. 😕")
    assert _denies_availability("O horário de 14:00 não ficou disponível para registrar agora.")
    assert _denies_availability("Infelizmente não há vaga nesse dia.")
    # Confirmação positiva não é negativa de vaga
    assert not _denies_availability("Sua consulta já está agendada para 14/10 às 14:00.")
    assert not _denies_availability("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py::test_denies_availability -q`
Expected: FAIL com `ImportError: cannot import name '_denies_availability'`.

- [ ] **Step 3: Implement the detector**

Em `app/graph/nodes.py`, logo após `_looks_like_slot_offer` (termina ~linha 403), acrescentar:

```python
_NO_AVAIL_PHRASES = (
    "não temos horário", "nao temos horario", "não temos horários", "nao temos horarios",
    "não ficou disponível", "nao ficou disponivel",
    "não há vaga", "nao ha vaga", "sem vaga",
    "não há horário", "nao ha horario", "não tem horário", "nao tem horario",
    "não tenho horário", "nao tenho horario",
)


def _denies_availability(text: str) -> bool:
    """True quando a resposta nega que haja vaga/horário disponível. Conservador de
    propósito: usado só dentro da janela estreita do GUARD_PANEL_BOOKED, onde negar
    vaga é justamente a contradição a eliminar."""
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in _NO_AVAIL_PHRASES)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_process_message.py::test_denies_availability -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(agendamento): detector _denies_availability para o guard painel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Injeção da diretiva no prompt

**Files:**
- Modify: `app/graph/nodes.py` (após a linha 2930)
- Test: `tests/test_process_message.py` (append ao fim)

- [ ] **Step 1: Write the failing integration test**

Adicionar ao FIM de `tests/test_process_message.py`:

```python
async def test_panel_booking_directive_injected_into_prompt():
    state = _make_patient_agent_state(messages=[HumanMessage(content="e aí, tem vaga pra hoje?")])
    with patch("app.graph.nodes._recent_panel_booking", new_callable=AsyncMock,
               return_value={"appointment_id": "evt-painel", "when": "14/10 às 14:00"}):
        system_msg = await _run_patient_agent(state)
    assert system_msg is not None
    assert "acabou de marcar pelo painel" in system_msg.content
    assert "14/10 às 14:00" in system_msg.content
    assert "NÃO ofereça outros horários" in system_msg.content


async def test_no_panel_booking_leaves_prompt_clean():
    state = _make_patient_agent_state(messages=[HumanMessage(content="e aí, tem vaga pra hoje?")])
    with patch("app.graph.nodes._recent_panel_booking", new_callable=AsyncMock,
               return_value=None):
        system_msg = await _run_patient_agent(state)
    assert system_msg is not None
    assert "acabou de marcar pelo painel" not in system_msg.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k panel_booking_directive -q`
Expected: FAIL — `test_panel_booking_directive_injected_into_prompt` não acha o texto (injeção ainda não existe).

- [ ] **Step 3: Implement the injection**

Em `app/graph/nodes.py`, logo após a linha 2930 (fim do bloco `else:` das consultas agendadas, antes de `import logging as _log` na linha 2932), acrescentar:

```python
    # ── Painel marcou a consulta: diretiva anti-contradição ──────────────────
    # Quando a clínica marca pelo painel no meio da conversa, o histórico ainda
    # tem as ofertas antigas da Eva e elas pesam mais que a lista de consultas
    # acima (caso Dario, 5581998196655, 14/10/2026). A diretiva forte, de alta
    # saliência (depois da lista), proíbe reabrir o agendamento.
    _panel_booking = await _recent_panel_booking(
        state["phone"], datetime.now(timezone.utc), upcoming
    )
    if _panel_booking:
        system_prompt += (
            "\n\nATENÇÃO: a clínica acabou de marcar pelo painel a consulta de "
            f"{_panel_booking['when']}. Essa consulta está CONFIRMADA. NÃO ofereça "
            "outros horários, NÃO diga que não há vaga, NÃO reabra o agendamento. "
            "Trate essa consulta como a consulta ativa. Se o paciente enviar "
            "comprovante, registre o pagamento normalmente. Se o paciente quiser "
            "cancelar ou remarcar, siga o fluxo normal."
        )
```

Nota: `upcoming` já existe (linha 2833, sempre uma lista); `datetime` e `timezone` já estão importados no módulo (usados em `_last_slot_offer_is_stale`). `_panel_booking` fica no escopo da função e será reutilizado na Task 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k panel_booking_directive -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(agendamento): injeta diretiva no prompt quando painel acabou de marcar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Guard pós-resposta `GUARD_PANEL_BOOKED`

**Files:**
- Modify: `app/graph/nodes.py` (imediatamente ANTES do `GUARD_STALE_SLOTS`, ~linha 3399)
- Test: `tests/test_process_message.py` (append ao fim)

- [ ] **Step 1: Write the failing integration tests**

Adicionar ao FIM de `tests/test_process_message.py`:

```python
# ── GUARD_PANEL_BOOKED: Eva não contradiz consulta marcada pelo painel ───────

async def _run_panel_booked(reply, panel_booking):
    from app.graph.nodes import patient_agent_node

    async def fake_ainvoke(messages):
        return AIMessage(content=reply)

    state = _make_patient_agent_state(messages=[HumanMessage(content="recebi?")])
    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes._recent_panel_booking", new_callable=AsyncMock, return_value=panel_booking), \
         patch("app.google_calendar.format_doctor_schedules", return_value="seg-sex"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        result = await patient_agent_node(state, {})
    return result, mock_send


_PANEL = {"appointment_id": "evt-painel", "when": "14/10 às 14:00"}


async def test_panel_booked_replaces_slot_offer():
    reply = ("Para a Dra. Bruna os horários mais próximos são 05/11 às 09:00, "
             "10:00 ou 15:00. Qual prefere?")
    result, mock_send = await _run_panel_booked(reply, _PANEL)
    sent = mock_send.await_args.args[1]
    assert "14/10 às 14:00" in sent
    assert "05/11" not in sent
    assert result["messages"][-1].content == sent
    assert not (result["messages"][-1].tool_calls or [])


async def test_panel_booked_replaces_availability_denial():
    reply = "O horário de 14:00 de 14/10 não ficou disponível para registrar agora. 😕"
    result, mock_send = await _run_panel_booked(reply, _PANEL)
    sent = mock_send.await_args.args[1]
    assert "14/10 às 14:00" in sent
    assert "não ficou disponível" not in sent.lower()


async def test_panel_booked_lets_normal_reply_through():
    reply = "Perfeito! Te espero no horário então. 😊"
    result, mock_send = await _run_panel_booked(reply, _PANEL)
    sent = mock_send.await_args.args[1]
    assert sent == reply  # não foi uma oferta nem negativa → passa intacto


async def test_no_panel_booking_lets_slot_offer_through():
    reply = "Tenho 09:00 e 10:00 disponíveis. Qual prefere?"
    result, mock_send = await _run_panel_booked(reply, None)
    sent = mock_send.await_args.args[1]
    assert sent == reply  # sem painel ativo, o guard não age
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k panel_booked -q`
Expected: FAIL — `test_panel_booked_replaces_slot_offer` e `_replaces_availability_denial` enviam o texto original (guard ainda não existe).

- [ ] **Step 3: Implement the guard**

Em `app/graph/nodes.py`, imediatamente ANTES do comentário `# ── Guard: lista de horários copiada de oferta antiga ──` (linha 3399, início do GUARD_STALE_SLOTS), acrescentar:

```python
    # ── GUARD_PANEL_BOOKED: a Eva não contradiz consulta marcada pelo painel ──
    # Caso Dario (5581998196655, 14/10/2026): a clínica marcou pelo painel no meio
    # da conversa e a Eva, ainda com as ofertas antigas no histórico, reofertou
    # horários e negou a vaga recém-confirmada. Dentro da janela da marcação
    # (_panel_booking, calculado junto da injeção no prompt), se a resposta ofertar
    # horários ou negar vaga, troca-se por uma confirmação da consulta real e
    # encerra o turno. Roda ANTES do GUARD_STALE_SLOTS para que uma oferta não seja
    # "refrescada" por uma busca nova.
    if (
        not response.tool_calls
        and response.content
        and _panel_booking
        and _first_internal_marker_idx(response.content) < 0
        and (_looks_like_slot_offer(str(response.content))
             or _denies_availability(str(response.content)))
    ):
        _confirm = (
            f"Sua consulta já está agendada para {_panel_booking['when']}. "
            "Está tudo certo por aqui. Se precisar de qualquer coisa, é só me dizer. 😊"
        )
        _logger.warning(
            "GUARD_PANEL_BOOKED: resposta contradizia consulta marcada pelo painel — "
            "substituindo por confirmação phone=%s", state.get("phone"),
        )
        await send_text(state["phone"], _confirm)
        await save_message(state["phone"], "assistant", _confirm)
        return {
            "messages": [AIMessage(content=_confirm)],
            "pending_appointment": None,
            "silent_mode": False,
        }
```

Nota: `_panel_booking`, `_logger`, `_first_internal_marker_idx`, `_looks_like_slot_offer`, `send_text`, `save_message` e `AIMessage` já estão no escopo.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k panel_booked -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(agendamento): GUARD_PANEL_BOOKED troca contradição por confirmação

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Regressão completa

**Files:** nenhum (só verificação)

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest --tb=short -q`
Expected: todos passam. Em especial, os testes `test_guard_stale_slots_*` continuam verdes (o novo guard não interfere quando não há painel: `_recent_panel_booking` devolve None porque os eventos mockados não têm `origem == "painel"`).

- [ ] **Step 2: Se algo quebrar**

Investigar com a skill `superpowers:systematic-debugging`. Não "consertar por cima". O suspeito mais provável é um teste existente cujo mock de `get_events_by_type` agora é consultado também por `_recent_panel_booking` — nesse caso confirmar que o retorno mockado não tem evento `appointment_booked` com `origem == "painel"`.

- [ ] **Step 3: Verificação final e PR**

Rodar a suíte uma última vez, conferir o diff, e abrir o PR a partir da worktree. Descrição do PR deve resumir o caso do Dario e o desenho (prevenção + rede de segurança), terminando com:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Self-Review (preenchido ao escrever o plano)

- **Cobertura do spec:** gatilho (Task 1), detector de negativa de vaga (Task 2), injeção no prompt (Task 3), guard pós-resposta (Task 4), testes de unidade e integração (Tasks 1–4), regressão (Task 5). Todas as condições do gatilho do spec têm teste.
- **Placeholders:** nenhum; todo passo com código mostra o código e todo comando tem saída esperada.
- **Consistência de tipos/nomes:** `_recent_panel_booking(phone, now, upcoming) -> dict|None` com chaves `appointment_id` e `when`; `_panel_booking` é a variável única reutilizada entre Task 3 e Task 4; `PANEL_BOOKING_GUARD_MAX_AGE`, `_denies_availability`, `_NO_AVAIL_PHRASES` usados de forma idêntica em implementação e testes.
