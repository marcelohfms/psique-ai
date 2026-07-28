# Nome Social Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Nome Social" field to patient registration — set only when the patient/contact spontaneously mentions it (never asked proactively), used by Eva to address the patient, shown as "Nome Civil (Nome Social)" on the Google Calendar event and clinic e-mail for `confirm_appointment`, and editable by the attendant in the dashboard.

**Architecture:** New nullable `patients.social_name` column. A new stage-free LangChain tool (`set_social_name`) lets Eva persist it whenever mentioned, following the exact pattern of the existing `save_patient_email` tool. The value flows into `ConversationState` via the same DB-hydration mechanism already used for `financial_name`, and is preferred over `patient_name` wherever Eva addresses the patient in the system prompt. `confirm_appointment`'s existing canonical-name resolution block is extended to build a combined display string for the Calendar event and clinic e-mail, and its sibling-disambiguation matching accepts `social_name` as an alias. The dashboard's existing generic `update_patient` endpoint is reused — only the field whitelist and the HTML form gain one new field.

**Tech Stack:** FastAPI, LangGraph, LangChain tools (`@tool` + `InjectedState`), Supabase (Postgres), pytest + pytest-asyncio (`asyncio_mode = auto`).

---

## Reference: full spec

See [`docs/superpowers/specs/2026-07-27-nome-social-design.md`](../specs/2026-07-27-nome-social-design.md) for the approved design and rationale. This plan implements it task by task.

---

### Task 1: Database schema — `patients.social_name` column

**Files:**
- Create: `supabase/migrations/20260727_add_social_name.sql`
- Modify: `app/graph/state.py:23` (add field to `ConversationState`)
- Modify: `app/database.py:53-58` (add to `_PATIENT_COPY_FIELDS`)

This is pure schema/plumbing — no runtime branch to unit-test in isolation (TypedDict keys aren't checked at runtime, and `_PATIENT_COPY_FIELDS` is a plain tuple copied field-by-field). It's exercised end-to-end by Task 4's hydration test and Task 7/8's `confirm_appointment` tests.

- [ ] **Step 1: Write the migration**

```sql
-- Migration: add social_name to patients (nome social — usado quando o paciente
-- pede espontaneamente para ser chamado por um nome diferente do nome civil).

ALTER TABLE patients
  ADD COLUMN IF NOT EXISTS social_name TEXT NULL;
```

- [ ] **Step 2: Add the field to `ConversationState`**

In `app/graph/state.py`, right after the `patient_name` line:

```python
    # Patient data (may differ from contact when is_patient=False)
    patient_name: str | None
    social_name: str | None  # nome pelo qual o paciente prefere ser chamado (opcional, nunca perguntado proativamente)
    patient_age: int | None        # determines 1h vs 2h slot
```

- [ ] **Step 3: Add `social_name` to the legacy-dict copy fields**

In `app/database.py`, extend the `_PATIENT_COPY_FIELDS` tuple (around line 53) so `get_users_by_phone`/`get_user_by_phone` return it:

```python
# Campos copiados de `patients` para o dict legado (formato antigo de `users`).
_PATIENT_COPY_FIELDS = (
    "email", "birth_date", "age", "doctor_id", "is_returning_patient",
    "patient_cpf", "consultation_reason", "referral_professional",
    "modality_restriction", "age_exception", "custom_price",
    "booking_fee_waived", "financial_name", "financial_cpf", "financial_email",
    "social_name",
)
```

- [ ] **Step 4: Apply the migration**

Run: `supabase db push` (or however this project applies migrations to the linked project — check `README.md`/`AUDITORIA.md` if unsure; do NOT hand-edit the remote schema outside a migration file).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260727_add_social_name.sql app/graph/state.py app/database.py
git commit -m "feat(db): add patients.social_name column and plumbing"
```

---

### Task 2: Sanitization helper for social names

**Files:**
- Modify: `app/graph/tools.py` (add `import re` near the top, and a new `_sanitize_social_name` function before the tool that uses it)
- Test: `tests/test_tools.py`

The existing prompt-only "LIMPEZA DE NOMES" rule already failed in practice (a patient's name was saved as "Nome 11 anos" and needed manual correction). For `social_name`, sanitize deterministically in code as an extra layer, not a replacement for the prompt instruction (Task 6).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tools.py` (anywhere near the top-level helper section, before the `@tool` tests):

```python
# ── _sanitize_social_name ─────────────────────────────────────────────────────

def test_sanitize_social_name_strips_age_suffix_with_comma():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("Malu, 25 anos") == "Malu"


def test_sanitize_social_name_strips_age_suffix_without_comma():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("Malu 8 anos") == "Malu"


def test_sanitize_social_name_strips_parenthetical():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("Malu (é como minha família me chama)") == "Malu"


def test_sanitize_social_name_keeps_clean_name_untouched():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("  João Gabriel  ") == "João Gabriel"


def test_sanitize_social_name_empty_after_stripping_returns_empty():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("(  )") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k sanitize_social_name -v`
Expected: FAIL with `ImportError: cannot import name '_sanitize_social_name'`

- [ ] **Step 3: Add `import re` and implement the helper**

In `app/graph/tools.py`, add `import re` to the imports at the top (line 1-4 area):

```python
import asyncio
import os
import re
from datetime import datetime, timedelta, date
```

Then add the helper (a good spot is right before the `@tool async def set_social_name` from Task 3, since it's only used there):

```python
_SOCIAL_NAME_AGE_RE = re.compile(r"\s*,?\s*\d+\s*anos?\b", re.IGNORECASE)
_SOCIAL_NAME_PARENS_RE = re.compile(r"\([^)]*\)")


def _sanitize_social_name(raw: str) -> str:
    """Remove sufixos comuns que não fazem parte do nome (idade, parênteses)
    antes de salvar o nome social — camada em código além da instrução de
    prompt, que já falhou sozinha na prática para patient_name/user_name."""
    cleaned = _SOCIAL_NAME_PARENS_RE.sub("", raw)
    cleaned = _SOCIAL_NAME_AGE_RE.sub("", cleaned)
    return " ".join(cleaned.split()).strip(" ,.-")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -k sanitize_social_name -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(tools): add deterministic sanitization for social names"
```

---

### Task 3: `set_social_name` tool

**Files:**
- Modify: `app/graph/tools.py` (new `@tool` function, after `save_patient_email` around line 2858)
- Test: `tests/test_tools.py`

Follows the exact pattern of `save_patient_email` (`app/graph/tools.py:2843-2856`): resolve phone from `config`, call `upsert_user` with `user_id=state.get("user_db_id")`, log an event, return a confirmation string.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tools.py` (near the `save_patient_email` tests, e.g. after line ~2529):

```python
# ── set_social_name ────────────────────────────────────────────────────────────

async def test_set_social_name_sanitizes_and_persists():
    from app.graph.tools import set_social_name
    state = _make_state(user_db_id="patient-id-1")
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await set_social_name.coroutine(
            social_name="Malu, 25 anos",
            state=state,
            config=CONFIG,
        )
    mock_upsert.assert_awaited_once_with(PHONE, {"social_name": "Malu"}, user_id="patient-id-1")
    mock_log.assert_awaited_once_with("social_name_set", PHONE, {"social_name": "Malu"})
    assert "Malu" in result


async def test_set_social_name_rejects_empty_after_sanitization():
    from app.graph.tools import set_social_name
    state = _make_state(user_db_id="patient-id-1")
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await set_social_name.coroutine(
            social_name="(  )",
            state=state,
            config=CONFIG,
        )
    mock_upsert.assert_not_awaited()
    assert "não entendi" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k set_social_name -v`
Expected: FAIL with `ImportError: cannot import name 'set_social_name'`

- [ ] **Step 3: Implement the tool**

In `app/graph/tools.py`, right after `save_patient_email` (after line 2856, before `update_preferred_doctor`):

```python
@tool
async def set_social_name(
    social_name: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Registra o nome social do paciente — o nome pelo qual ele prefere ser
    chamado, quando diferente do nome civil. Use SOMENTE quando o paciente ou
    contato mencionar espontaneamente essa preferência (ex: "pode me chamar de
    Malu", "meu nome social é..."). NUNCA pergunte isso de forma proativa.
    """
    phone = config["configurable"]["phone"]
    cleaned = _sanitize_social_name(social_name)
    if not cleaned:
        return "Não entendi o nome social informado. Pode repetir?"
    await upsert_user(phone, {"social_name": cleaned}, user_id=state.get("user_db_id"))
    await log_event("social_name_set", phone, {"social_name": cleaned})
    return f"Nome social '{cleaned}' registrado com sucesso. A partir de agora vou te chamar assim."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -k set_social_name -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(tools): add set_social_name tool"
```

---

### Task 4: Register the tool + hydrate `social_name` from the DB

**Files:**
- Modify: `app/graph/nodes.py:10-20` (import), `app/graph/nodes.py:28-37` (TOOLS list), `app/graph/nodes.py:1148-1165` (hydration block)
- Test: `tests/test_process_message.py`

Without this, `set_social_name` exists but Eva's tool-calling LLM never sees it, and a value saved in a previous conversation would never load back into `ConversationState` on a fresh checkpoint.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_message.py`, near `test_patient_agent_injects_greeting_on_first_turn` (after line ~1316):

```python
async def test_patient_agent_hydrates_social_name_from_db():
    """social_name ausente no state (ex: checkpoint novo) deve ser hidratado do
    banco, igual a patient_name/financial_name, e refletido no prompt da Eva."""
    state = _make_patient_agent_state(user_db_id=None, social_name=None)
    fake_patient = {
        "id": "patient-1", "name": "Carlos Silva", "social_name": "Malu",
        "doctor_id": None, "email": "carlos@email.com",
        "is_returning_patient": True,
        "financial_name": None, "financial_cpf": None, "financial_email": None,
    }
    with patch(
        "app.patients.resolve_active_patient", new_callable=AsyncMock,
        return_value={"contact": {"name": "Carlos"}, "patient": fake_patient},
    ):
        system_msg = await _run_patient_agent(state, last_assistant_time=None)
    assert system_msg is not None
    assert "Malu" in system_msg.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py -k hydrates_social_name -v`
Expected: FAIL (`Malu` not in system_msg.content — hydration doesn't copy `social_name` yet, and even if it did, Task 5 hasn't wired it into the prompt yet)

- [ ] **Step 3: Register the tool in `TOOLS`**

In `app/graph/nodes.py`, add `set_social_name` to the import block (line 10-20):

```python
from app.graph.tools import (
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment, mark_reschedule_in_progress,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    set_social_name,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data, extend_payment_deadline, waive_booking_fee,
    request_external_contact, nudge_external_contact,
    _expected_consultation_amount,
)
```

And to the `TOOLS` list (line 28-37):

```python
TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment, mark_reschedule_in_progress,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    set_social_name,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data, extend_payment_deadline, waive_booking_fee,
    request_external_contact, nudge_external_contact,
]
```

- [ ] **Step 4: Add `social_name` to the DB-hydration block**

In `app/graph/nodes.py`, inside the `if _h_patient:` block (right after the `financial_name` hydration lines, ~1160-1161):

```python
                if not state.get("financial_name") and _h_patient.get("financial_name"):
                    _sync_updates["financial_name"] = _h_patient["financial_name"]
                if not state.get("financial_cpf") and _h_patient.get("financial_cpf"):
                    _sync_updates["financial_cpf"] = _h_patient["financial_cpf"]
                if not state.get("financial_email") and _h_patient.get("financial_email"):
                    _sync_updates["financial_email"] = _h_patient["financial_email"]
                if not state.get("social_name") and _h_patient.get("social_name"):
                    _sync_updates["social_name"] = _h_patient["social_name"]
```

- [ ] **Step 5: Run test to verify it still fails (expected — Task 5 wires the prompt)**

Run: `uv run pytest tests/test_process_message.py -k hydrates_social_name -v`
Expected: still FAIL — `social_name` is now hydrated into `state`, but nothing in the prompt-building code uses it yet. Confirm the failure message no longer complains about missing hydration (add a temporary `print(system_msg.content)` locally if unsure, then remove it) — proceed to Task 5 without committing yet.

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat(graph): register set_social_name tool and hydrate social_name from DB"
```

(The test added in Step 1 stays red until Task 5 is done — that's expected and will be fixed by the very next task, which is a continuation of the same change. Do not skip running it again at the end of Task 5.)

---

### Task 5: Prefer `social_name` when Eva addresses the patient

**Files:**
- Modify: `app/graph/nodes.py:1399` and `app/graph/nodes.py:1407` (name resolution for the system prompt)
- Test: `tests/test_process_message.py` (finishes the test added in Task 4, plus a dedicated one)

This is the change that actually makes Eva call the patient by their social name. `first_name` (built from `_full_name`) is what gets injected into the system prompt as `{patient_name}` (`app/graph/nodes.py:1458`), and is also used for the "CONTATO ≠ PACIENTE" and `GUARDIAN_RULE` blocks further down — all inherit this change automatically since they reuse `first_name`.

- [ ] **Step 1: Write the additional failing test**

Add to `tests/test_process_message.py`, right after the test from Task 4:

```python
async def test_patient_agent_prefers_social_name_over_civil_name():
    """Quando social_name já está no state, a Eva deve se dirigir ao paciente por
    ele, não pelo nome civil — mesmo com patient_name preenchido."""
    state = _make_patient_agent_state(patient_name="Carlos Silva", social_name="Malu")
    system_msg = await _run_patient_agent(state, last_assistant_time=None)
    assert system_msg is not None
    assert "atendendo Malu" in system_msg.content
    assert "atendendo Carlos" not in system_msg.content
```

- [ ] **Step 2: Run both tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k "hydrates_social_name or prefers_social_name" -v`
Expected: both FAIL — `first_name` still resolves from `patient_name`/`user_name` only.

- [ ] **Step 3: Prefer `social_name` in the name-resolution lines**

In `app/graph/nodes.py`, change line 1399:

```python
    _full_name = state.get("social_name") or state.get("patient_name") or state.get("user_name") or "paciente"
```

And line 1407 (the rare fallback branch where `user_name` is missing and the contact IS the patient):

```python
    _contact_full = state.get("user_name") or (
        "responsável" if _is_third_party else (state.get("social_name") or state.get("patient_name") or "paciente")
    )
```

- [ ] **Step 4: Run both tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k "hydrates_social_name or prefers_social_name" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full process_message suite to check for regressions**

Run: `uv run pytest tests/test_process_message.py -v`
Expected: PASS (no prior test asserted a specific civil name was used when `social_name` was absent from state, so this should not break anything — `state.get("social_name")` is `None` in every other test's state, falling through to the existing behavior).

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(graph): address patient by social_name when set"
```

---

### Task 6: Prompt instruction — detect and never proactively ask

**Files:**
- Modify: `app/graph/prompts.py` (near line 928-930 in `EXISTING_PATIENT_SYSTEM`, and near line 1160-1162 in `NEW_PATIENT_SYSTEM` — NOT `COLLECT_SYSTEM`, since `collect_info_node` does structured-output extraction only and has no tool-calling access to call `set_social_name`)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_message.py`, near `test_every_patient_facing_prompt_carries_clinic_address` (after line ~2646):

```python
def test_tool_bearing_prompts_carry_social_name_instruction():
    """NEW_PATIENT_SYSTEM e EXISTING_PATIENT_SYSTEM (únicos com acesso a tools)
    devem instruir a Eva a chamar set_social_name quando o paciente mencionar
    espontaneamente um nome social — e a NUNCA perguntar isso de forma proativa.
    COLLECT_SYSTEM fica de fora: collect_info_node não chama tools."""
    from app.graph import prompts
    for name in ("NEW_PATIENT_SYSTEM", "EXISTING_PATIENT_SYSTEM"):
        content = getattr(prompts, name)
        assert "set_social_name" in content, f"{name} não menciona set_social_name"
        assert "NUNCA pergunte" in content or "nunca pergunte" in content.lower(), (
            f"{name} não deixa explícito que a pergunta nunca deve ser proativa"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py -k social_name_instruction -v`
Expected: FAIL — neither template mentions `set_social_name` yet.

- [ ] **Step 3: Add the instruction to both templates**

In `app/graph/prompts.py`, right after the "NOME AO SE DIRIGIR" block in `EXISTING_PATIENT_SYSTEM` (after line 930):

```python
NOME AO SE DIRIGIR: Ao chamar o contato ou paciente pelo nome, use sempre os dois primeiros \
nomes quando o primeiro for Maria, Ana, João ou José (ex: "Maria Beatriz", "João Pedro", \
"Ana Clara", "José Henrique"). Para todos os outros nomes, use apenas o primeiro.

NOME SOCIAL: se o paciente ou contato mencionar espontaneamente que prefere ser chamado por \
um nome diferente do nome civil (ex: "pode me chamar de Malu", "meu nome social é..."), chame \
set_social_name com esse nome. A partir daí, dirija-se ao paciente por esse nome. NUNCA pergunte \
isso de forma proativa — só registre quando a própria pessoa mencionar por conta própria.
```

And the identical block in `NEW_PATIENT_SYSTEM` (after its own "NOME AO SE DIRIGIR" occurrence, currently around line 1160-1162):

```python
NOME AO SE DIRIGIR: Ao chamar o contato ou paciente pelo nome, use sempre os dois primeiros \
nomes quando o primeiro for Maria, Ana, João ou José (ex: "Maria Beatriz", "João Pedro", \
"Ana Clara", "José Henrique"). Para todos os outros nomes, use apenas o primeiro.

NOME SOCIAL: se o paciente ou contato mencionar espontaneamente que prefere ser chamado por \
um nome diferente do nome civil (ex: "pode me chamar de Malu", "meu nome social é..."), chame \
set_social_name com esse nome. A partir daí, dirija-se ao paciente por esse nome. NUNCA pergunte \
isso de forma proativa — só registre quando a própria pessoa mencionar por conta própria.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_process_message.py -k social_name_instruction -v`
Expected: PASS

- [ ] **Step 5: Run the full prompts-related test file to check for regressions**

Run: `uv run pytest tests/test_process_message.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/prompts.py tests/test_process_message.py
git commit -m "feat(prompts): instruct Eva to detect social name spontaneously, never ask proactively"
```

---

### Task 7: `confirm_appointment` — combined display name for Calendar and clinic e-mail

**Files:**
- Modify: `app/graph/tools.py:894-918` (canonical-name resolution block inside `confirm_appointment`) and the two use sites at `app/graph/tools.py:937` (`create_event`) and `app/graph/tools.py:1074-1082` (`_notify_clinic`)
- Test: `tests/test_tools.py`

Format decision (from the spec): **"Nome Civil (Nome Social)"**, civil name first — it's the identifier that matches CPF/prontuário/conciliação financeira, so it stays primary; the social name in parentheses tells the doctor how to address the patient.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`, right after `test_confirm_appointment_normalizes_attendant_all_caps_name` (after line ~468):

```python
async def test_confirm_appointment_shows_social_name_in_calendar_and_email():
    """Quando o paciente tem social_name registrado, o evento do Calendar e o
    e-mail da clínica mostram 'Nome Civil (Nome Social)' — nome civil primeiro
    (casa com CPF/prontuário), nome social entre parênteses (como chamar o
    paciente)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _patient = {"id": "patient-id", "patient_name": "Maria Eduarda Viana", "name": "Renata Viana", "social_name": "Malu"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-social") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_name="Maria Eduarda Viana"),
            config=CONFIG,
        )
    assert mock_create.call_args.kwargs["patient_name"] == "Maria Eduarda Viana (Malu)"
    _notify_msg = mock_notify.call_args[0][0]
    assert "Paciente: Maria Eduarda Viana (Malu)" in _notify_msg
    assert mock_notify.call_args.kwargs["subject"] == "Agendamento realizado — Maria Eduarda Viana (Malu)"


async def test_confirm_appointment_no_social_name_uses_plain_name():
    """Regressão: sem social_name, Calendar e e-mail continuam mostrando só o
    nome civil (comportamento existente, sem parênteses vazios)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _patient = {"id": "patient-id", "patient_name": "Carlos Silva", "name": "Carlos Silva"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-plain") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_name="Carlos Silva"),
            config=CONFIG,
        )
    assert mock_create.call_args.kwargs["patient_name"] == "Carlos Silva"
    assert "Paciente: Carlos Silva\n" in mock_notify.call_args[0][0]


async def test_confirm_appointment_resolves_social_name_alias_with_multiple_candidates():
    """Quando há mais de um paciente no telefone (irmãs) e o override bate só com
    o social_name de uma delas, a resolução de nome canônico (usada pro Calendar
    e e-mail) precisa achar essa paciente pelo social_name — não só a resolução
    de patient_id (Task 8) — senão o Calendar mostraria 'Malu' cru, sem o nome
    civil, justamente no caso em que o médico mais precisa dele."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _joao = {"id": "joao-id", "patient_name": "João Pedro Viana", "name": "Renata Viana", "social_name": None}
    _maria = {"id": "maria-id", "patient_name": "Maria Eduarda Viana", "name": "Renata Viana", "social_name": "Malu"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-canon-alias") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_joao, _maria]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Malu",
        )
    assert mock_create.call_args.kwargs["patient_name"] == "Maria Eduarda Viana (Malu)"
    assert "Paciente: Maria Eduarda Viana (Malu)" in mock_notify.call_args[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k "shows_social_name or no_social_name_uses_plain or resolves_social_name_alias_with_multiple" -v`
Expected: `test_confirm_appointment_shows_social_name_in_calendar_and_email` and `test_confirm_appointment_resolves_social_name_alias_with_multiple_candidates` FAIL (Calendar/e-mail still show plain/raw names); `test_confirm_appointment_no_social_name_uses_plain_name` already PASSES (documents current behavior) — that's fine, it becomes the regression guard.

- [ ] **Step 3: Compute the combined display name and use it for Calendar + e-mail**

In `app/graph/tools.py`, extend the canonical-name resolution block (lines 894-918):

```python
    patient_name = patient_name_override.strip() or state.get("patient_name") or state.get("user_name") or "Paciente"

    # Always use the canonical `patients.name` from the DB for the Calendar
    # event and clinic notification below — patient_name_override/state can
    # carry the attendant's raw wording (e.g. an ALL CAPS name copied from a
    # private note), and both must follow the standard format regardless of
    # how it arrived (caso João Pedro Lins Da Costa Gomes / Ednara de Morais
    # Lins, 5581992349207, 2026-07-27: nota da atendente em CAIXA ALTA foi
    # parar sem normalização no evento do Calendar e no e-mail da clínica).
    social_name = None
    try:
        _name_candidates = await get_users_by_phone(config["configurable"]["phone"])
        _canonical_user = None
        if len(_name_candidates) > 1:
            _target = patient_name.strip().lower()
            _canonical_user = next(
                (c for c in _name_candidates if (c.get("patient_name") or "").strip().lower() == _target), None
            ) or next(
                (c for c in _name_candidates if (c.get("social_name") or "").strip().lower() == _target), None
            ) or next(
                (c for c in _name_candidates if _target in (c.get("patient_name") or "").strip().lower()), None
            )
        elif _name_candidates:
            _canonical_user = _name_candidates[0]
        if _canonical_user and _canonical_user.get("patient_name"):
            patient_name = _canonical_user["patient_name"]
        if _canonical_user:
            social_name = _canonical_user.get("social_name")
    except Exception:
        _logger.exception("CONFIRM_DEBUG canonical name lookup failed, using raw patient_name=%s", patient_name)

    # Nome Civil (Nome Social): nome civil primeiro (casa com CPF/prontuário,
    # fica auditável), nome social entre parênteses avisa o médico como chamar
    # o paciente. Só para uso interno (Calendar, e-mail da clínica) — a Eva usa
    # só o nome social ao se dirigir ao paciente (ver app/graph/nodes.py).
    calendar_display_name = f"{patient_name} ({social_name})" if social_name else patient_name
```

Then change the `create_event` call (line 937) to use it:

```python
    try:
        event_id = await create_event(
            calendar_id=calendar_id,
            start=start,
            slot_minutes=slot_duration_minutes,
            patient_name=calendar_display_name,
            doctor_name=doctor_label,
            is_minor_first=is_minor_first,
            session_note=session_note,
            modality=effective_modality,
            patient_email=state.get("patient_email") or "",
            patient_number=config["configurable"]["phone"],
        )
```

And the `_notify_clinic` call (lines 1073-1083):

```python
    asyncio.create_task(_notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {calendar_display_name}{session_label}\n"
        f"Data e horário: {formatted}\n"
        f"Médico(a): {doctor_label}"
        f"{modality_line}\n\n"
        f"📋 LEMBRETE: enviar o Termo de Compromisso para o e-mail do paciente ({patient_email})."
        f"{registration_block}",
        phone=phone,
        subject=f"Agendamento realizado — {calendar_display_name}",
    ))
```

Leave every other use of the plain `patient_name` variable (the `_match_by_name` calls, `log_event("appointment_booked", ...)`, exception logging) unchanged — those are internal matching/audit paths that must keep using the civil name only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -k "shows_social_name or no_social_name_uses_plain or resolves_social_name_alias_with_multiple or normalizes_attendant_all_caps" -v`
Expected: PASS (4 tests — including the pre-existing CAPS-normalization regression test, unaffected)

- [ ] **Step 5: Run the full tools test file to check for regressions**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(tools): show Nome Civil (Nome Social) on Calendar event and clinic e-mail"
```

---

### Task 8: `confirm_appointment` — accept `social_name` as an alias when matching patients

**Files:**
- Modify: `app/graph/tools.py:972-984` (the `_match_by_name` closure used to disambiguate between multiple patients on the same phone)
- Test: `tests/test_tools.py`

This is the "reconhece os dois nomes" requirement: if two siblings share a phone and one goes by a social name, an attendant override (or the LLM referencing the patient by their social name) must still resolve to the right patient — never the civil name alone.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`, near `test_confirm_appointment_multi_patient_override_beats_user_db_id` (after line ~440):

```python
async def test_confirm_appointment_matches_sibling_by_social_name():
    """Duas pacientes no mesmo telefone (irmãs); uma tem social_name. Um override
    usando o nome social deve resolver para a paciente certa, não para a outra
    nem falhar o match (caso análogo a Laila/Suzi Viana, mas com nome social)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _joao = {"id": "joao-id", "patient_name": "João Pedro Viana", "name": "Renata Viana", "social_name": None}
    _maria = {"id": "maria-id", "patient_name": "Maria Eduarda Viana", "name": "Renata Viana", "social_name": "Malu"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-alias"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_joao, _maria]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Malu",
        )
    _insert_payload = table.insert.call_args[0][0]
    assert _insert_payload.get("patient_id") == "maria-id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools.py -k matches_sibling_by_social_name -v`
Expected: FAIL — `_match_by_name("Malu")` finds no civil-name match and falls through to `get_user_by_phone`, landing on the wrong (or an arbitrary) patient.

- [ ] **Step 3: Add the social-name alias check to `_match_by_name`**

In `app/graph/tools.py`, inside the persist block's `_match_by_name` closure (lines 972-984):

```python
            def _match_by_name(target: str) -> dict | None:
                target = target.strip().lower()
                if not target:
                    return None
                for _u in all_users:
                    _pname = (_u.get("patient_name") or _u.get("name") or "").strip().lower()
                    if _pname == target:
                        return _u
                for _u in all_users:
                    _sname = (_u.get("social_name") or "").strip().lower()
                    if _sname and _sname == target:
                        return _u
                for _u in all_users:
                    _pname = (_u.get("patient_name") or _u.get("name") or "").strip().lower()
                    if target in _pname:
                        return _u
                return None
```

(Exact civil-name match still wins first, then exact social-name match, then civil-name substring match — this ordering means an exact match of either name always beats a loose substring match of the civil name.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tools.py -k matches_sibling_by_social_name -v`
Expected: PASS

- [ ] **Step 5: Run the full tools test file to check for regressions**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS (in particular, the pre-existing `test_confirm_appointment_multi_patient_uses_user_db_id_over_stale_patient_name` and `..._override_beats_user_db_id` tests, which exercise the same closure without any `social_name` key present at all)

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(tools): accept social_name as alias when matching patients sharing a phone"
```

---

### Task 9: Dashboard — manual edit field for the attendant

**Files:**
- Modify: `dashboard/attendant_db.py:88-92` (`_PATIENT_FIELDS` whitelist)
- Modify: `dashboard/templates/atendente.html:314` (form field) and `:367` (save payload)
- Test: `dashboard/tests/test_attendant_db.py`

No new route needed — `POST /api/atendente/paciente/{patient_id}` (`dashboard/attendant_routes.py:65-69`) already accepts an arbitrary `data: dict`, filtered through the `_PATIENT_FIELDS` whitelist in `attendant_db.update_patient`.

- [ ] **Step 1: Write the failing test**

Add to `dashboard/tests/test_attendant_db.py`, right after `test_update_patient_only_whitelisted` (after line ~100):

```python
async def test_update_patient_allows_social_name(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "João", "social_name": None}]
    await attendant_db.update_patient("p1", {"social_name": "Jojo"})
    row = patched_client.store["patients"][0]
    assert row["social_name"] == "Jojo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -k allows_social_name -v`
Expected: FAIL — `social_name` isn't in `_PATIENT_FIELDS`, so `update_patient` silently drops it (`row["social_name"]` stays `None`).

- [ ] **Step 3: Add `social_name` to the whitelist**

In `dashboard/attendant_db.py`, extend `_PATIENT_FIELDS` (around line 88-92):

```python
_PATIENT_FIELDS = {
    "name", "birth_date", "age", "patient_cpf", "email", "doctor_id",
    "is_returning_patient", "modality_restriction", "age_exception", "custom_price",
    "financial_name", "financial_cpf", "financial_email",
    "social_name",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -k allows_social_name -v`
Expected: PASS

- [ ] **Step 5: Add the field to the dashboard form**

In `dashboard/templates/atendente.html`, add the field to the Paciente section (around line 314, next to "Nome"):

```javascript
        ${field("Nome", "p_name", patient.name)}
        ${field("Nome Social", "p_social_name", patient.social_name)}
        ${field("Nascimento (dd/mm/aaaa)", "p_birth", patient.birth_date)}
```

And add it to the `savePatient()` payload (around line 365-374):

```javascript
async function savePatient(pid) {
  const ok = await post(`/api/atendente/paciente/${pid}`, { data: {
    name: val("p_name"), social_name: val("p_social_name"),
    birth_date: val("p_birth"), patient_cpf: val("p_cpf"),
    email: val("p_email"), doctor_id: val("p_doctor") || null,
    is_returning_patient: chk("p_returning"),
    modality_restriction: val("p_modality") || null,
    age_exception: chk("p_age_exc"), custom_price: numOrNull("p_price"),
  }});
  if (ok) flash("Paciente salvo ✓");
}
```

- [ ] **Step 6: Manually verify the form in the browser**

Run the dashboard locally (`cd dashboard && uv run uvicorn main:app --reload --port 8001` or however it's normally started — check `dashboard/README.md`/`Dockerfile` if unsure), open the atendente panel for a test phone, confirm the "Nome Social" input appears next to "Nome", type a value, click "Salvar paciente", reload, and confirm the value persisted. This step has no automated test — the JS template has no test harness in this repo; the whitelist test above is what's automated.

- [ ] **Step 7: Run the full dashboard test suite to check for regressions**

Run: `cd dashboard && uv run pytest -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add dashboard/attendant_db.py dashboard/templates/atendente.html dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): allow attendant to set patient's social_name"
```

---

## Self-review notes

- **Spec coverage:** modelo de dados → Task 1; detecção/persistência + sanitização em código → Tasks 2-3; registro da tool + hidratação → Task 4; Eva se dirige pelo nome social → Task 5; nunca perguntar proativamente → Task 6; Calendar/e-mail com "Nome Civil (Nome Social)" → Task 7; matching aceita os dois nomes → Task 8; edição manual no dashboard → Task 9; planilha de pagamentos fora de escopo → nenhuma task a toca (correto, por design).
- **Placeholder scan:** nenhum "TBD"/"depois" — todo passo tem código completo.
- **Type consistency:** `social_name` é o nome do campo em todo lugar (state, DB, dict legado, whitelist, tool, template) — sem variação de nome entre tasks.
- **Two distinct matching sites, both covered:** `confirm_appointment` tem DOIS pontos separados que comparam nomes contra candidatos — o bloco de resolução de nome canônico (usado para o texto do Calendar/e-mail, corrigido na Task 7) e o closure `_match_by_name` do bloco de persistência (usado para decidir o `patient_id`, corrigido na Task 8). Os dois precisavam do alias de `social_name` independentemente — corrigir só um deixaria o outro mostrando o nome errado ou resolvendo pro paciente errado.
- **Scope note (deliberado, não pedido no spec original mas necessário para a Task 7 funcionar):** o `update_event` usado por `reschedule_appointment`/`change_modality` (outros pontos que atualizam o mesmo evento do Calendar depois de criado) NÃO recebe o tratamento "Nome Civil (Nome Social)" nesta versão — eles reusam um `patient_name` já resolvido sem repetir a busca canônica que só existe em `confirm_appointment`. Isso espelha o escopo original do fix de nome canônico (commit `8419369`), que também só cobriu `confirm_appointment`. Se depois for preciso manter o nome social consistente após uma remarcação, isso é um projeto à parte (auditar os ~6 outros call-sites de `update_event`).
