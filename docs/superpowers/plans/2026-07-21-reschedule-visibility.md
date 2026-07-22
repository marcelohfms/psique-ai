# Reschedule Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `pending_reschedule` appointment visible in Eva's prompt context regardless of how old its original slot date is, and give her a proactive instruction to treat any scheduling talk from that patient as a reschedule continuation — never a new booking.

**Architecture:** `get_upcoming_appointments` (app/database.py) currently drops `pending_reschedule` rows older than 48h from its result entirely — they fall into none of its three buckets (future/recent/unpaid_past). Add a fourth bucket, `stale_reschedule`, mirroring the existing age-agnostic `unpaid_past` pattern. Then surface it in the prompt built by `patient_agent_node` (app/graph/nodes.py) with an explicit status tag and its own section header, and add a proactive rule to `CANCELLATION_RULES` (app/graph/prompts.py) anchored to that tag.

**Tech Stack:** Python, Supabase (mocked in tests via MagicMock/AsyncMock), pytest-asyncio.

Spec: `docs/superpowers/specs/2026-07-21-reschedule-visibility-design.md`

---

### Task 1: `get_upcoming_appointments` returns stale `pending_reschedule` rows

**Files:**
- Modify: `app/database.py:319-391` (`get_upcoming_appointments`)
- Test: `tests/test_database_shim.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database_shim.py`, after `test_get_upcoming_appointments_flags_completed_unpaid_as_already_occurred` (currently ends at line 413):

```python
@pytest.mark.asyncio
async def test_get_upcoming_appointments_flags_stale_pending_reschedule():
    """Um pending_reschedule cujo end_time original já passou há mais de 48h deve
    aparecer no resultado com stale_reschedule=True — sem esse bucket, a linha some
    do prompt inteiro assim que passa da janela de 'recém-terminado', e a Eva perde
    todo sinal de que existe uma remarcação pendente (caso Heitor/Ludmilla,
    5581996937559, 21/07/2026: pending_reschedule de 02/07 ficou invisível até
    19/07, quando a Eva tratou a volta da paciente como agendamento novo)."""
    users = [{"id": "p-heitor", "patient_name": "Heitor"}]
    stale_rows = [{
        "appointment_id": "a-stale",
        "start_time": "2026-07-02T21:00:00+00:00",
        "end_time": "2026-07-02T23:00:00+00:00",
        "status": "pending_reschedule",
        "patient_id": "p-heitor",
        "booking_fee_paid_at": "2026-06-27T12:54:11+00:00",
        "booking_fee_waived": False,
    }]
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    # 1ª = futuros (vazio); 2ª = recém-terminados (vazio); 3ª = concluídos com saldo
    # pendente (vazio); 4ª = pending_reschedule antigo (stale_rows)
    table.execute = AsyncMock(side_effect=[
        MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=stale_rows),
    ])
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock, return_value=users):
        result = await database.get_upcoming_appointments("5581996937559")
    assert len(result) == 1
    assert result[0]["stale_reschedule"] is True
    assert result[0]["appointment_id"] == "a-stale"
    assert result[0]["patient_name"] == "Heitor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database_shim.py::test_get_upcoming_appointments_flags_stale_pending_reschedule -v --tb=short`

Expected: FAIL — either `AssertionError: assert 0 == 1` (result is empty, since today's `get_upcoming_appointments` only issues 3 queries and never reaches a 4th `side_effect` entry) or a `StopIteration`/`AssertionError` on `len(result)`. Confirm the failure reason is "4th bucket doesn't exist yet", not a typo in the test.

- [ ] **Step 3: Implement the stale_reschedule bucket**

In `app/database.py`, update the docstring and add the new query + merge line. Replace:

```python
async def get_upcoming_appointments(phone: str) -> list[dict]:
    """Return scheduled/ongoing/recent/unpaid-past appointments for a user, ordered by start_time.

    Includes:
    - Future appointments (end_time >= now)
    - Appointments that ended in the last 48 h but are still marked 'scheduled'
      (complete_appointments script hasn't run yet) — flagged with 'recently_ended'
    - Completed appointments still owing a balance (paid_at is null) — flagged with
      'already_occurred', regardless of how long ago, so the LLM never talks about
      settling the balance "no dia da consulta" as if it were still upcoming.
    """
```

with:

```python
async def get_upcoming_appointments(phone: str) -> list[dict]:
    """Return scheduled/ongoing/recent/unpaid-past appointments for a user, ordered by start_time.

    Includes:
    - Future appointments (end_time >= now)
    - Appointments that ended in the last 48 h but are still marked 'scheduled'
      (complete_appointments script hasn't run yet) — flagged with 'recently_ended'
    - Completed appointments still owing a balance (paid_at is null) — flagged with
      'already_occurred', regardless of how long ago, so the LLM never talks about
      settling the balance "no dia da consulta" as if it were still upcoming.
    - pending_reschedule appointments whose original end_time is already outside the
      48h "recent" window — flagged with 'stale_reschedule', regardless of how long
      ago, so the LLM never loses track of a pending reschedule just because the
      patient took weeks to come back.
    """
```

Then, in the same function, replace:

```python
    # Completed appointments still owing a balance (any age — a patient who owes
    # money from weeks ago must never be discussed as if the payment date is
    # still ahead of us).
    unpaid_past_result = (
        await client.from_("appointments")
        .select(_appt_fields)
        .eq("status", "completed")
        .in_("patient_id", patient_ids)
        .is_("paid_at", "null")
        .order("start_time")
        .execute()
    )

    # Attach patient_name to each row so the caller can attribute the appointment
    # to the correct patient when the contact manages more than one.
    future = [dict(r, patient_name=name_by_id.get(r.get("patient_id"), "")) for r in (future_result.data or [])]
    recent = [dict(r, recently_ended=True, patient_name=name_by_id.get(r.get("patient_id"), "")) for r in (recent_result.data or [])]
    unpaid_past = [
        dict(r, already_occurred=True, patient_name=name_by_id.get(r.get("patient_id"), ""))
        for r in (unpaid_past_result.data or [])
    ]
    return future + recent + unpaid_past
```

with:

```python
    # Completed appointments still owing a balance (any age — a patient who owes
    # money from weeks ago must never be discussed as if the payment date is
    # still ahead of us).
    unpaid_past_result = (
        await client.from_("appointments")
        .select(_appt_fields)
        .eq("status", "completed")
        .in_("patient_id", patient_ids)
        .is_("paid_at", "null")
        .order("start_time")
        .execute()
    )

    # pending_reschedule older than the "recent" window — its original start_time
    # recedes into the past the longer the patient waits to rebook, but the
    # reschedule is still pending. Without this bucket the row is invisible to the
    # LLM once end_time < cutoff_recent (caso Heitor/Ludmilla, 5581996937559,
    # 21/07/2026), and Eva treats a returning patient as a brand-new booking.
    stale_reschedule_result = (
        await client.from_("appointments")
        .select(_appt_fields)
        .eq("status", "pending_reschedule")
        .in_("patient_id", patient_ids)
        .lt("end_time", cutoff_recent)
        .order("start_time")
        .execute()
    )

    # Attach patient_name to each row so the caller can attribute the appointment
    # to the correct patient when the contact manages more than one.
    future = [dict(r, patient_name=name_by_id.get(r.get("patient_id"), "")) for r in (future_result.data or [])]
    recent = [dict(r, recently_ended=True, patient_name=name_by_id.get(r.get("patient_id"), "")) for r in (recent_result.data or [])]
    unpaid_past = [
        dict(r, already_occurred=True, patient_name=name_by_id.get(r.get("patient_id"), ""))
        for r in (unpaid_past_result.data or [])
    ]
    stale_reschedule = [
        dict(r, stale_reschedule=True, patient_name=name_by_id.get(r.get("patient_id"), ""))
        for r in (stale_reschedule_result.data or [])
    ]
    return future + recent + unpaid_past + stale_reschedule
```

- [ ] **Step 4: Update the two existing tests that assume exactly 3 `execute()` calls**

The new query means `get_upcoming_appointments` now calls `table.execute()` 4 times instead of 3. Two existing tests hardcode a 3-item `side_effect` list and will raise `StopIteration` once the 4th call happens. Fix both:

In `tests/test_database_shim.py`, find (around line 372):

```python
    table.execute = AsyncMock(side_effect=[MagicMock(data=future_rows), MagicMock(data=[]), MagicMock(data=[])])
```

Replace with:

```python
    table.execute = AsyncMock(side_effect=[MagicMock(data=future_rows), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[])])
```

And find (around line 405):

```python
    table.execute = AsyncMock(side_effect=[MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=past_unpaid_rows)])
```

Replace with:

```python
    table.execute = AsyncMock(side_effect=[MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=past_unpaid_rows), MagicMock(data=[])])
```

- [ ] **Step 5: Run tests to verify everything passes**

Run: `uv run pytest tests/test_database_shim.py -v --tb=short`

Expected: PASS — all tests in the file green, including the new one and the two updated ones.

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_database_shim.py
git commit -m "$(cat <<'EOF'
fix(database): surface stale pending_reschedule appointments in get_upcoming_appointments

pending_reschedule rows older than the 48h "recent" window fell into none of
the three existing buckets (future/recent/unpaid_past) and disappeared from
Eva's prompt context entirely. Add a fourth bucket, stale_reschedule, mirroring
the existing age-agnostic unpaid_past pattern (caso Heitor/Ludmilla,
5581996937559, 21/07/2026).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Prompt shows an explicit reschedule tag and its own section

**Files:**
- Modify: `app/graph/nodes.py:1528-1562` (appointment-injection block inside `patient_agent_node`)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_message.py`, right after `test_patient_agent_prompt_flags_past_unpaid_appointment` (ends at line 1799 with `assert "Consultas agendadas (por paciente):" not in system_prompt`):

```python
async def test_patient_agent_prompt_flags_stale_pending_reschedule():
    """Uma consulta com stale_reschedule=True deve aparecer no prompt com a tag
    🔄 REMARCAÇÃO PENDENTE e sob seu próprio cabeçalho — não junto de 'Consultas
    agendadas', que sugere consulta futura confirmada (caso Heitor/Ludmilla,
    5581996937559, 21/07/2026)."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from unittest.mock import AsyncMock, MagicMock, patch
    from langchain_core.messages import HumanMessage, AIMessage
    from tests.conftest import CONFIG
    from app.graph.nodes import patient_agent_node

    TZ = ZoneInfo("America/Recife")
    now = _dt.datetime.now(TZ)
    weeks_ago = (now - _dt.timedelta(days=19)).replace(hour=18, minute=0, second=0, microsecond=0)

    appt = {
        "appointment_id": "a-stale",
        "start_time": weeks_ago.isoformat(),
        "status": "pending_reschedule",
        "booking_fee_paid_at": (weeks_ago - _dt.timedelta(days=5)).isoformat(),
        "booking_fee_waived": False,
        "stale_reschedule": True,
    }

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="ok")

    state = {
        "phone": "5581996937559@s.whatsapp.net",
        "stage": "patient_agent",
        "user_name": "Ludmilla",
        "patient_name": "Heitor",
        "patient_age": 11,
        "is_patient": False,
        "is_returning_patient": False,
        "preferred_doctor": "julio",
        "messages": [HumanMessage(content="oi, quero remarcar")],
    }

    with patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[appt]), \
         patch("app.google_calendar.format_doctor_schedules", return_value=""), \
         patch("app.graph.nodes._get_agent_llm", return_value=_FakeLLM()), \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes.is_registration_complete", return_value=True), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value=None):
        await patient_agent_node(state, CONFIG)

    system_prompt = captured["messages"][0].content
    assert "Remarcação pendente (vaga liberada, aguardando nova data):" in system_prompt
    assert "🔄 REMARCAÇÃO PENDENTE" in system_prompt
    assert "a-stale" in system_prompt
    # Must not be listed under the future-appointments header.
    assert "Consultas agendadas (por paciente):" not in system_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py::test_patient_agent_prompt_flags_stale_pending_reschedule -v --tb=short`

Expected: FAIL with `AssertionError: assert 'Remarcação pendente (vaga liberada, aguardando nova data):' in ...` (the section doesn't exist yet — the row currently falls through to `future_lines` since nothing checks `stale_reschedule`, and the label carries no status tag).

- [ ] **Step 3: Implement the tag and new section**

In `app/graph/nodes.py`, replace:

```python
    # Inject upcoming/recent appointments so the LLM knows what already exists
    upcoming = await get_upcoming_appointments(state["phone"])
    if upcoming:
        from zoneinfo import ZoneInfo as _ZI
        _TZ = _ZI("America/Recife")
        future_lines = []
        recent_lines = []
        past_unpaid_lines = []
        for apt in upcoming:
            dt = datetime.fromisoformat(apt["start_time"]).astimezone(_TZ)
            fee_ok = apt.get("booking_fee_paid_at") or apt.get("booking_fee_waived")
            fee_tag = "" if fee_ok else " ⚠️ TAXA DE RESERVA PENDENTE"
            _suffix = date_suffix_pt(dt.date(), _now_recife.date())
            # Prefix with the patient name — the contact may manage several patients,
            # so the LLM must not assume the appointment belongs to the active one.
            _pname = (apt.get("patient_name") or "").strip()
            _who = f"{_pname} — " if _pname else ""
            label = f"- {_who}{dt.strftime('%d/%m/%Y às %H:%M')} ({_suffix}) (ID: {apt['appointment_id']}){fee_tag}"
            if apt.get("already_occurred"):
                past_unpaid_lines.append(label)
            elif apt.get("recently_ended"):
                recent_lines.append(label)
            else:
                future_lines.append(label)
        if future_lines:
            system_prompt += "\n\nConsultas agendadas (por paciente):\n" + "\n".join(future_lines)
        if recent_lines:
            system_prompt += "\n\nConsulta(s) recém-realizada(s) (nas últimas 48h):\n" + "\n".join(recent_lines)
        if past_unpaid_lines:
            system_prompt += (
                "\n\nConsulta(s) já realizada(s) com saldo pendente:\n"
                + "\n".join(past_unpaid_lines)
                + "\nATENÇÃO: estas consultas JÁ OCORRERAM — NUNCA diga que o saldo será "
                "quitado \"no dia da consulta\". Diga que o saldo já pode ser quitado agora."
            )
```

with:

```python
    # Inject upcoming/recent appointments so the LLM knows what already exists
    upcoming = await get_upcoming_appointments(state["phone"])
    if upcoming:
        from zoneinfo import ZoneInfo as _ZI
        _TZ = _ZI("America/Recife")
        future_lines = []
        recent_lines = []
        past_unpaid_lines = []
        stale_reschedule_lines = []
        for apt in upcoming:
            dt = datetime.fromisoformat(apt["start_time"]).astimezone(_TZ)
            fee_ok = apt.get("booking_fee_paid_at") or apt.get("booking_fee_waived")
            fee_tag = "" if fee_ok else " ⚠️ TAXA DE RESERVA PENDENTE"
            # Marca o status explicitamente — sem isso, uma consulta pending_reschedule
            # fica indistinguível de uma scheduled normal no texto, mesmo quando visível
            # (caso Heitor/Ludmilla, 5581996937559, 21/07/2026).
            reschedule_tag = " 🔄 REMARCAÇÃO PENDENTE" if apt.get("status") == "pending_reschedule" else ""
            _suffix = date_suffix_pt(dt.date(), _now_recife.date())
            # Prefix with the patient name — the contact may manage several patients,
            # so the LLM must not assume the appointment belongs to the active one.
            _pname = (apt.get("patient_name") or "").strip()
            _who = f"{_pname} — " if _pname else ""
            label = f"- {_who}{dt.strftime('%d/%m/%Y às %H:%M')} ({_suffix}) (ID: {apt['appointment_id']}){fee_tag}{reschedule_tag}"
            if apt.get("already_occurred"):
                past_unpaid_lines.append(label)
            elif apt.get("recently_ended"):
                recent_lines.append(label)
            elif apt.get("stale_reschedule"):
                stale_reschedule_lines.append(label)
            else:
                future_lines.append(label)
        if future_lines:
            system_prompt += "\n\nConsultas agendadas (por paciente):\n" + "\n".join(future_lines)
        if recent_lines:
            system_prompt += "\n\nConsulta(s) recém-realizada(s) (nas últimas 48h):\n" + "\n".join(recent_lines)
        if past_unpaid_lines:
            system_prompt += (
                "\n\nConsulta(s) já realizada(s) com saldo pendente:\n"
                + "\n".join(past_unpaid_lines)
                + "\nATENÇÃO: estas consultas JÁ OCORRERAM — NUNCA diga que o saldo será "
                "quitado \"no dia da consulta\". Diga que o saldo já pode ser quitado agora."
            )
        if stale_reschedule_lines:
            system_prompt += (
                "\n\nRemarcação pendente (vaga liberada, aguardando nova data):\n"
                + "\n".join(stale_reschedule_lines)
            )
```

- [ ] **Step 4: Run tests to verify everything passes**

Run: `uv run pytest tests/test_process_message.py -v --tb=short -k "prompt or appointment"`

Expected: PASS — new test green, and `test_patient_agent_prompt_flags_past_unpaid_appointment` / other prompt-injection tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "$(cat <<'EOF'
fix(nodes): tag pending_reschedule appointments explicitly in Eva's prompt

The appointment-injection block never surfaced appointments.status in the
prompt text, so a pending_reschedule row (even when visible) was
indistinguishable from a normal scheduled one. Add an explicit
🔄 REMARCAÇÃO PENDENTE tag and a dedicated section for stale_reschedule rows,
separate from "Consultas agendadas" (which implies a confirmed future visit).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Proactive rule in `CANCELLATION_RULES`

**Files:**
- Modify: `app/graph/prompts.py:459-461`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_message.py`, right after `test_patient_agent_prompt_flags_stale_pending_reschedule` (added in Task 2):

```python
def test_cancellation_rules_proactively_directs_stale_reschedule_flow():
    """A regra deve orientar a Eva a tratar QUALQUER menção a marcar/agendar como
    continuação de uma remarcação pendente quando a tag 🔄 REMARCAÇÃO PENDENTE
    estiver presente — proativamente, sem esperar o paciente pedir remarcação
    explicitamente (caso Heitor/Ludmilla, 5581996937559, 21/07/2026: Eva tratou
    como agendamento novo por falta desse direcionamento antecipado)."""
    from app.graph.prompts import CANCELLATION_RULES
    assert "🔄 REMARCAÇÃO PENDENTE" in CANCELLATION_RULES
    assert "NUNCA confirm_appointment" in CANCELLATION_RULES
    assert "mark_reschedule_in_progress" in CANCELLATION_RULES
    assert "ANTES de get_available_slots" in CANCELLATION_RULES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py::test_cancellation_rules_proactively_directs_stale_reschedule_flow -v --tb=short`

Expected: FAIL with `AssertionError: assert '🔄 REMARCAÇÃO PENDENTE' in ...` — the tag string doesn't exist anywhere in `CANCELLATION_RULES` yet.

- [ ] **Step 3: Implement the rule**

In `app/graph/prompts.py`, replace:

```python
- Consulta na quinta às 17h. Paciente cancela na quarta às 18h → dentro do prazo.

CONSEQUÊNCIAS:
```

with:

```python
- Consulta na quinta às 17h. Paciente cancela na quarta às 18h → dentro do prazo.

REMARCAÇÃO PENDENTE JÁ EM ANDAMENTO (verifique ANTES de aplicar as regras abaixo): \
se qualquer consulta listada acima estiver marcada com 🔄 REMARCAÇÃO PENDENTE e o paciente voltar a \
falar sobre marcar/agendar — mesmo que a mensagem pareça um pedido novo — trate SEMPRE como \
continuação dessa remarcação: chame mark_reschedule_in_progress (com o appointment_id dessa consulta) \
ANTES de get_available_slots, e finalize com reschedule_appointment — NUNCA confirm_appointment. \
Isso vale mesmo que a data original pareça antiga — o registro de remarcação pendente não expira.

CONSEQUÊNCIAS:
```

- [ ] **Step 4: Run tests to verify everything passes**

Run: `uv run pytest tests/test_process_message.py -v --tb=short -k "cancellation_rules or stale_pending_reschedule"`

Expected: PASS — both the new constant-content test and the Task 2 prompt-assembly test green.

- [ ] **Step 5: Commit**

```bash
git add app/graph/prompts.py tests/test_process_message.py
git commit -m "$(cat <<'EOF'
fix(prompts): proactively direct Eva to the reschedule flow on the tag

The existing pending_reschedule guidance was reactive and buried inside a
large cancellation/reschedule policy block. Add an explicit rule, anchored to
the new 🔄 REMARCAÇÃO PENDENTE tag, near the top of CANCELLATION_RULES so it's
read before any other reschedule-specific rule — directing Eva to
mark_reschedule_in_progress + reschedule_appointment instead of
confirm_appointment even when the original date looks old.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Full regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --tb=short`

Expected: PASS — all tests green (370 pre-existing + 3 new = 373), no warnings beyond the pre-existing deprecation warnings already present before this plan.

- [ ] **Step 2: Confirm no leftover uncommitted changes**

Run: `git status`

Expected: working tree clean (everything committed in Tasks 1-3).

---

## Notes for the implementing engineer

- All three production files (`app/database.py`, `app/graph/nodes.py`, `app/graph/prompts.py`) are edited independently — tasks can be done in order without cross-task blocking, but Task 2's test uses a row shaped like Task 1's output (`stale_reschedule: True`), so do Task 1 before Task 2. Task 3 is independent of both but is ordered last to match the spec's presentation order.
- No new state/checkpoint fields are introduced anywhere in this plan — per the spec, the source of truth stays the database, re-read every turn via `get_upcoming_appointments`, exactly like the existing `already_occurred`/`recently_ended` flags.
- Do not touch `app/graph/tools.py` (Guard 0) — that was already fixed and merged in PR #90. This plan is additive/complementary to it.
