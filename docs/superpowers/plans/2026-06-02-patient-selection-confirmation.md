# Patient Selection Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a phone number has multiple registered patients, Eva confirms the selected patient with the guardian before proceeding, preventing appointments from being linked to the wrong patient.

**Architecture:** Add a `pending_confirmation_patient` field to `ConversationState`. After the name-matching logic selects a candidate from `pending_patients`, Eva sends a confirmation message and stores the candidate in `pending_confirmation_patient`. On the next turn, a new branch checks that field: affirmative → advance to `patient_agent`; negative → re-show the list and clear `pending_confirmation_patient`.

**Tech Stack:** Python, LangGraph, existing `send_text` / `save_message` helpers in `nodes.py`.

---

### Task 1: Add `pending_confirmation_patient` to ConversationState

**Files:**
- Modify: `app/graph/state.py`

- [ ] **Step 1: Add the new field**

In `app/graph/state.py`, add after the `pending_patients` field (line 50):

```python
    # Holds a candidate patient dict while Eva waits for the guardian to confirm
    # the selection before advancing to patient_agent.
    pending_confirmation_patient: dict | None
```

- [ ] **Step 2: Verify the app still imports cleanly**

```bash
uv run python -c "from app.graph.state import ConversationState; print('ok')"
```
Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/graph/state.py
git commit -m "feat: add pending_confirmation_patient field to ConversationState"
```

---

### Task 2: Write failing tests for the confirmation flow

**Files:**
- Modify: `tests/test_process_message.py`

- [ ] **Step 1: Add helper for multi-patient state**

Add this helper near the existing `_base_minor_state` helper in `tests/test_process_message.py`:

```python
def _base_multi_patient_state(**kwargs):
    """Base state for multi-patient disambiguation tests."""
    base = {
        "phone": "558199999999@s.whatsapp.net",
        "stage": "collect_info",
        "user_name": None,
        "patient_name": None,
        "patient_age": None,
        "birth_date": None,
        "patient_cpf": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "guardian_relationship": None,
        "is_patient": None,
        "preferred_doctor": None,
        "patient_email": None,
        "consultation_reason": None,
        "referral_professional": None,
        "medication_note": None,
        "pending_patients": None,
        "pending_confirmation_patient": None,
        "user_db_id": None,
        "silent_mode": None,
        "modality_restriction": None,
        "age_exception": None,
        "messages": [],
    }
    base.update(kwargs)
    return base
```

- [ ] **Step 2: Add test — successful match sends confirmation and stores candidate**

```python
async def test_patient_selection_sends_confirmation_message():
    """When Eva matches a patient from pending_patients, she sends a confirmation
    message and stores the candidate in pending_confirmation_patient instead of
    advancing to patient_agent immediately."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    patients = [
        {"id": "aaa", "patient_name": "Mariana França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": "r@example.com", "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
        {"id": "bbb", "patient_name": "Manuela França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": "r@example.com", "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
    ]
    state = _base_multi_patient_state(
        pending_patients=patients,
        messages=[
            AIMessage(content="Para qual paciente?\n1. Mariana França\n2. Manuela França"),
            HumanMessage(content="Manuela"),
        ],
    )

    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("stage") != "patient_agent", "Should not advance yet"
    assert result.get("pending_confirmation_patient") == patients[1]
    assert result.get("pending_patients") == patients  # keep list in case of rejection
    sent = mock_send.call_args[0][1]
    assert "Manuela França" in sent
    assert "certo" in sent.lower() or "confirmar" in sent.lower()
```

- [ ] **Step 3: Add test — affirmative reply advances to patient_agent**

```python
async def test_patient_confirmation_affirmative_advances():
    """When pending_confirmation_patient is set and the guardian replies
    affirmatively, Eva clears disambiguation state and advances to patient_agent."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    candidate = {
        "id": "bbb", "patient_name": "Manuela França", "name": "Rebeka França",
        "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
        "is_patient": False, "is_returning_patient": True,
        "email": "r@example.com", "guardian_name": None, "guardian_cpf": None,
        "guardian_relationship": "mãe", "patient_cpf": None,
        "modality_restriction": None, "age_exception": None,
    }
    state = _base_multi_patient_state(
        pending_confirmation_patient=candidate,
        pending_patients=[{}, candidate],  # kept from previous turn
        messages=[
            AIMessage(content="Só confirmar: você está entrando em contato para Manuela França, certo?"),
            HumanMessage(content="sim"),
        ],
    )

    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("stage") == "patient_agent"
    assert result.get("patient_name") == "Manuela França"
    assert result.get("user_db_id") == "bbb"
    assert result.get("pending_confirmation_patient") is None
    assert result.get("pending_patients") is None
```

- [ ] **Step 4: Add test — negative reply re-shows the list**

```python
async def test_patient_confirmation_negative_reshows_list():
    """When pending_confirmation_patient is set and the guardian says no,
    Eva re-shows the patient list and clears pending_confirmation_patient."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    patients = [
        {"id": "aaa", "patient_name": "Mariana França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": None, "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
        {"id": "bbb", "patient_name": "Manuela França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": None, "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
    ]
    candidate = patients[1]
    state = _base_multi_patient_state(
        pending_confirmation_patient=candidate,
        pending_patients=patients,
        messages=[
            AIMessage(content="Só confirmar: você está entrando em contato para Manuela França, certo?"),
            HumanMessage(content="não"),
        ],
    )

    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("stage") != "patient_agent"
    assert result.get("pending_confirmation_patient") is None
    assert result.get("pending_patients") == patients  # list preserved for re-selection
    sent = mock_send.call_args[0][1]
    assert "Mariana França" in sent
    assert "Manuela França" in sent
```

- [ ] **Step 5: Run the new tests to verify they fail**

```bash
uv run pytest tests/test_process_message.py::test_patient_selection_sends_confirmation_message tests/test_process_message.py::test_patient_confirmation_affirmative_advances tests/test_process_message.py::test_patient_confirmation_negative_reshows_list -v
```
Expected: all 3 FAIL (functionality not yet implemented).

- [ ] **Step 6: Commit**

```bash
git add tests/test_process_message.py
git commit -m "test: add failing tests for multi-patient selection confirmation"
```

---

### Task 3: Implement the confirmation flow in `nodes.py`

**Files:**
- Modify: `app/graph/nodes.py`

#### 3a — Handle `pending_confirmation_patient` (new branch, goes before the existing `elif pending` block)

- [ ] **Step 1: Add the confirmation-response branch**

In `app/graph/nodes.py`, find the block that starts with `elif pending:` (currently around line 135). Insert the following block **immediately before** it (i.e., between the `if selected` block that handles fresh loads and the existing `elif pending:` block):

```python
        elif state.get("pending_confirmation_patient"):
            candidate = state["pending_confirmation_patient"]
            last_human = ""
            for msg in reversed(state["messages"]):
                if getattr(msg, "type", None) == "human":
                    last_human = (msg.content or "").strip().lower()
                    break

            _affirmative = {"sim", "s", "yes", "y", "isso", "correto", "certo", "exato", "confirmado", "confirmo", "ok"}
            _negative    = {"não", "nao", "no", "n", "errado", "incorreto", "outro", "outra"}

            if any(w in last_human for w in _affirmative):
                doc_key = DOCTOR_NAMES.get(candidate.get("doctor_id", ""), None)
                return {
                    "pending_confirmation_patient": None,
                    "pending_patients": None,
                    "user_db_id": candidate["id"],
                    "user_name": candidate.get("name"),
                    "patient_name": candidate.get("patient_name") or candidate.get("name"),
                    "patient_age": candidate.get("age"),
                    "birth_date": candidate.get("birth_date"),
                    "is_patient": candidate.get("is_patient"),
                    "is_returning_patient": candidate.get("is_returning_patient"),
                    "preferred_doctor": doc_key,
                    "patient_email": candidate.get("email"),
                    "guardian_name": candidate.get("guardian_name"),
                    "guardian_cpf": candidate.get("guardian_cpf"),
                    "guardian_relationship": candidate.get("guardian_relationship"),
                    "patient_cpf": candidate.get("patient_cpf"),
                    "modality_restriction": candidate.get("modality_restriction"),
                    "age_exception": candidate.get("age_exception"),
                    "stage": "patient_agent",
                    "messages": [],
                }
            elif any(w in last_human for w in _negative):
                # Guardian rejected — re-show full list
                all_pending = state.get("pending_patients") or []
                names = [u.get("patient_name") or u.get("name") or "Paciente" for u in all_pending]
                options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
                reply = f"Sem problema! Para qual paciente você está entrando em contato?\n\n{options}"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {
                    "pending_confirmation_patient": None,
                    "pending_patients": all_pending,
                    "messages": [AIMessage(content=reply)],
                }
            else:
                # Ambiguous — ask again
                patient_name = candidate.get("patient_name") or candidate.get("name") or "o paciente"
                reply = f"Desculpe, não entendi. Você está entrando em contato para *{patient_name}*? (sim/não)"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {"messages": [AIMessage(content=reply)]}
```

#### 3b — Replace direct advance with confirmation step in the existing `elif pending` block

- [ ] **Step 2: Replace the `if selected:` return in the existing `elif pending` block**

Find this block (starts around line 150 in the original, now a few lines further down after the insertion):

```python
            if selected:
                doc_key = DOCTOR_NAMES.get(selected.get("doctor_id", ""), None)
                return {
                    "pending_patients": None,
                    ...
                    "stage": "patient_agent",
                    "messages": [],
                }
```

Replace it with:

```python
            if selected:
                patient_name = selected.get("patient_name") or selected.get("name") or "o paciente"
                reply = f"Só confirmar: você está entrando em contato para *{patient_name}*, certo? (sim/não)"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {
                    "pending_confirmation_patient": selected,
                    "messages": [AIMessage(content=reply)],
                }
```

- [ ] **Step 3: Run the 3 new tests — they should now pass**

```bash
uv run pytest tests/test_process_message.py::test_patient_selection_sends_confirmation_message tests/test_process_message.py::test_patient_confirmation_affirmative_advances tests/test_process_message.py::test_patient_confirmation_negative_reshows_list -v
```
Expected: all 3 PASS.

- [ ] **Step 4: Run the full test suite to check for regressions**

```bash
uv run pytest --tb=short
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat: require guardian confirmation before advancing from multi-patient selection"
```

---

### Task 4: Update `_base_minor_state` helper to include the new field

**Files:**
- Modify: `tests/test_process_message.py`

The existing `_base_minor_state` helper (and any other base state helpers) will cause `TypedDict` validation warnings if they don't include `pending_confirmation_patient`. Update them.

- [ ] **Step 1: Add `pending_confirmation_patient: None` to `_base_minor_state`**

Find `_base_minor_state` in `tests/test_process_message.py`. Add the missing key:

```python
        "pending_confirmation_patient": None,
```

Place it right after `"pending_patients": None,`.

- [ ] **Step 2: Run full test suite**

```bash
uv run pytest --tb=short
```
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_process_message.py
git commit -m "test: add pending_confirmation_patient to base state helpers"
```
