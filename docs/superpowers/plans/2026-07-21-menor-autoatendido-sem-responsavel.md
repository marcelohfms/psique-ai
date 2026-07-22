# Menor autoatendido sem responsável — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Eva from asking (and requiring) a "responsável" for minor patients who message about themselves (`is_patient=True`, no guardian contact exists) — today this question can never be resolved and the patient gets stuck forever.

**Architecture:** Three independent code locations currently enforce "every minor needs guardian_name/guardian_cpf" with no check for *who is messaging*. Each gets the same one-line guard added: the requirement only applies when `is_patient is False` (a real third party — a guardian — is the one texting). No schema changes, no changes to how guardian data is stored for the third-party case.

**Tech Stack:** Python, pytest, pytest-asyncio (existing test suite — no new dependencies).

---

### Task 1: `is_registration_complete` — skip guardian requirement for self-messaging minors

This is the most important of the three gates: it decides whether a patient's registration is considered complete and can proceed past `collect_info` into `patient_agent` (where tools like `request_document` live). Fixing only the conversation-flow gates (Tasks 2-3) without this one would still leave self-messaging minors permanently stuck, because `graph.py`'s `_route_entry` and `nodes.py`'s final-flush check both call this function.

**Files:**
- Modify: `app/database.py:302-312`
- Test: `tests/test_database_shim.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_database_shim.py`, right after `test_minor_returning_still_requires_guardian_name_and_relationship` (after line 301):

```python
def test_self_messaging_new_minor_without_guardian_is_complete():
    # Menor NOVO que conversa em nome próprio (is_patient=True) — não há
    # responsável na conversa para exigir esses campos (caso Clara, 2026-07-21).
    u = _complete_minor(
        is_patient=True,
        is_returning_patient=False,
        guardian_name=None,
        guardian_relationship=None,
        guardian_cpf=None,
    )
    assert is_registration_complete(u) is True


def test_self_messaging_returning_minor_without_guardian_is_complete():
    # Mesmo caso, mas paciente já é da clínica.
    u = _complete_minor(
        is_patient=True,
        is_returning_patient=True,
        guardian_name=None,
        guardian_relationship=None,
        guardian_cpf=None,
    )
    assert is_registration_complete(u) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database_shim.py -k self_messaging_minor -v`
Expected: both FAIL — `is_registration_complete` currently returns `False` because `guardian_name`/`guardian_relationship` are `None` and the check doesn't look at `is_patient`.

- [ ] **Step 3: Fix `is_registration_complete`**

In `app/database.py`, replace lines 302-312:

```python
    # Minor-specific requirements
    if age is not None and age < 18:
        # guardian_name e guardian_relationship são obrigatórios para todo menor.
        # guardian_cpf é obrigatório apenas para pacientes NOVOS — pacientes que
        # já são da clínica já têm o CPF do responsável no cadastro.
        required_minor = ["guardian_name", "guardian_relationship"]
        if user.get("is_returning_patient") is False:
            required_minor.append("guardian_cpf")
        for field in required_minor:
            if not user.get(field):
                return False
```

with:

```python
    # Minor-specific requirements — only apply when a THIRD PARTY (a guardian)
    # is the one messaging (is_patient=False). A minor messaging about
    # themselves (is_patient=True) has no guardian contact linked, so these
    # fields can never be filled — see
    # docs/superpowers/specs/2026-07-21-menor-autoatendido-sem-responsavel-design.md.
    if age is not None and age < 18 and user.get("is_patient") is False:
        # guardian_name e guardian_relationship são obrigatórios para todo menor
        # com um terceiro conversando. guardian_cpf é obrigatório apenas para
        # pacientes NOVOS — pacientes que já são da clínica já têm o CPF do
        # responsável no cadastro.
        required_minor = ["guardian_name", "guardian_relationship"]
        if user.get("is_returning_patient") is False:
            required_minor.append("guardian_cpf")
        for field in required_minor:
            if not user.get(field):
                return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_database_shim.py -v`
Expected: PASS for the 2 new tests, and no regressions in the other `is_registration_complete` tests (`test_minor_returning_without_guardian_cpf_is_complete`, `test_minor_new_without_guardian_cpf_is_incomplete`, `test_minor_returning_still_requires_guardian_name_and_relationship`, `test_julio_minor_undetermined_returning_status_is_incomplete`, `test_bruna_minor_undetermined_returning_status_is_complete`, `test_adult_returning_without_patient_cpf_is_complete`, `test_adult_undetermined_returning_status_is_complete`) — all of those use `is_patient=False` or are adults, so behavior is unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_database_shim.py
git commit -m "fix(database): skip guardian requirement for self-messaging minors

Minors who message Eva about themselves (is_patient=True) have no
guardian contact linked, so guardian_name/guardian_cpf can never be
filled — is_registration_complete now only requires them when a
third party (is_patient=False) is the one messaging."
```

---

### Task 2: `_next_question` — don't preview the guardian question for self-messaging minors

`_next_question` computes what message to show immediately after extracting the current step's answer (via `_nq()` inside `_extract_and_ask`). Without this fix, a self-messaging minor would still briefly see "Qual é o nome completo do responsável?" as the very next message after answering the "já é paciente?" question, even though Task 3 will make the real gate (Step 6) skip it on the next turn — a confusing mismatch. This task closes that gap.

**Files:**
- Modify: `app/graph/nodes.py:455-458`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_process_message.py`, right after `test_collect_info_asks_guardian_cpf_after_guardian_name` (after line 641):

```python
async def test_collect_info_self_messaging_new_minor_skips_guardian_name_preview():
    """A self-messaging minor (is_patient=True, no guardian contact) answering
    'não' to 'já é paciente?' must be asked about the doctor next, NOT the
    guardian's name — that question can never be resolved for this case
    (caso Clara, 5581999249242, 2026-07-21)."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Clara",
        patient_name="Clara",
        patient_cpf="111.222.333-00",
        patient_age=16,
        birth_date="08/09/2009",
        is_patient=True,  # self-messaging — no guardian contact exists
        messages=[
            HumanMessage(content="quero pedir uma receita"),
            AIMessage(content="É a primeira consulta ou o paciente já está em acompanhamento na clínica?"),
            HumanMessage(content="não"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is False
    assert result.get("guardian_name") is None
    sent = mock_send.call_args[0][1].lower()
    assert "responsável" not in sent
    assert "júlio" in sent or "bruna" in sent


async def test_collect_info_self_messaging_returning_minor_skips_guardian_name_preview():
    """Same as above, but the minor is already a patient of the clinic."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Clara",
        patient_name="Clara",
        patient_cpf="111.222.333-00",
        patient_age=16,
        birth_date="08/09/2009",
        is_patient=True,
        messages=[
            HumanMessage(content="quero pedir uma receita"),
            AIMessage(content="É a primeira consulta ou o paciente já está em acompanhamento na clínica?"),
            HumanMessage(content="já sou paciente"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is True
    assert result.get("guardian_name") is None
    sent = mock_send.call_args[0][1].lower()
    assert "responsável" not in sent
    assert "júlio" in sent or "bruna" in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k self_messaging_minor_skips_guardian_name_preview -v`
Expected: both FAIL — the message sent contains "Qual é o nome completo do responsável pelo paciente?" instead of the doctor question.

- [ ] **Step 3: Fix `_next_question`**

In `app/graph/nodes.py`, replace lines 455-458:

```python
        if minor and not s.get("guardian_name"):
            return _GUARDIAN_NAME_Q
        if minor and is_new and not s.get("guardian_cpf"):
            return _GUARDIAN_CPF_Q
```

with:

```python
        is_third_party = s.get("is_patient") is False
        if minor and is_third_party and not s.get("guardian_name"):
            return _GUARDIAN_NAME_Q
        if minor and is_third_party and is_new and not s.get("guardian_cpf"):
            return _GUARDIAN_CPF_Q
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -v`
Expected: PASS for the 2 new tests, and no regressions — in particular
`test_collect_info_asks_guardian_name_after_minor_birth_date` (is_patient not
yet relevant at that point), `test_collect_info_asks_guardian_cpf_after_guardian_name`
and `test_collect_info_returning_minor_guardian_name_skips_guardian_cpf`
(both use `is_patient=False`, so unaffected) must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "fix(nodes): don't preview guardian question for self-messaging minors

_next_question now only suggests the guardian name/CPF question when
a third party (is_patient=False) is messaging — a self-messaging
minor has no guardian contact to ask about."
```

---

### Task 3: `collect_info_node` Steps 6/7 — skip the guardian gate for self-messaging minors

This is the actual deadlock: Steps 6/7 run on every turn once age < 18, independent of what the previous turn asked, and today they trigger regardless of who's messaging. This is the fix that lets Clara-like patients actually reach the doctor/email questions and finish.

**Files:**
- Modify: `app/graph/nodes.py:716-748`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_message.py`, right after `test_collect_info_adult_skips_guardian_steps` (after line 1084):

```python
async def test_collect_info_self_messaging_minor_skips_guardian_steps():
    """A self-messaging minor (is_patient=True, already answered
    is_returning_patient) must skip Steps 6/7 (guardian name/CPF) entirely and
    fall through to the next unanswered field (preferred_doctor) — otherwise
    the guardian question fires on every turn with no way to ever resolve it
    (caso Clara, 5581999249242, 2026-07-21)."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        patient_age=16,
        birth_date="08/09/2009",
        is_patient=True,  # self-messaging minor — no guardian contact exists
        is_returning_patient=True,
        messages=[
            AIMessage(content="É a primeira consulta ou o paciente já está em acompanhamento na clínica?"),
            HumanMessage(content="sim"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    # Guardian fields must NOT have been set, and the message actually sent
    # must be the doctor question, not the guardian one (Steps 6/7 must not
    # have intercepted this turn).
    assert result.get("guardian_name") is None
    assert result.get("guardian_cpf") is None
    sent = mock_send.call_args[0][1].lower()
    assert "responsável" not in sent
    assert "júlio" in sent or "bruna" in sent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py -k test_collect_info_self_messaging_minor_skips_guardian_steps -v`
Expected: FAIL on `assert "responsável" not in sent` — Step 6 currently fires unconditionally for any minor with no `guardian_name`, so `sent` is "Qual é o nome completo do responsável pelo paciente?" instead of the doctor question.

- [ ] **Step 3: Fix Steps 6/7**

In `app/graph/nodes.py`, replace lines 716-733:

```python
        # Step 6: guardian name (todos os menores)
        if (state.get("patient_age") or 99) < 18 and not state.get("guardian_name"):
            _last_ai_asked_guardian_name = last_ai and (
                _GUARDIAN_NAME_Q in last_ai
                or "responsável" in last_ai.lower()
                or "nome completo do" in last_ai.lower()
            )
            if _last_ai_asked_guardian_name and last_human:
                return await _extract_and_ask(
                    {"guardian_name": last_human, "user_name": last_human},
                    _nq(guardian_name=last_human),
                )
            return await _ask(_GUARDIAN_NAME_Q)

        # Step 7: guardian CPF (menores) — apenas para pacientes NOVOS; opcional p/ estrangeiros
        if (state.get("patient_age") or 99) < 18 \
                and state.get("is_returning_patient") is False \
                and not state.get("guardian_cpf"):
```

with:

```python
        # Step 6: guardian name (menores com um terceiro conversando —
        # is_patient=False; um menor autoatendido não tem responsável na
        # conversa para exigir isso, ver caso Clara 2026-07-21)
        if (state.get("patient_age") or 99) < 18 and state.get("is_patient") is False \
                and not state.get("guardian_name"):
            _last_ai_asked_guardian_name = last_ai and (
                _GUARDIAN_NAME_Q in last_ai
                or "responsável" in last_ai.lower()
                or "nome completo do" in last_ai.lower()
            )
            if _last_ai_asked_guardian_name and last_human:
                return await _extract_and_ask(
                    {"guardian_name": last_human, "user_name": last_human},
                    _nq(guardian_name=last_human),
                )
            return await _ask(_GUARDIAN_NAME_Q)

        # Step 7: guardian CPF (menores com terceiro conversando) — apenas
        # para pacientes NOVOS; opcional p/ estrangeiros
        if (state.get("patient_age") or 99) < 18 \
                and state.get("is_patient") is False \
                and state.get("is_returning_patient") is False \
                and not state.get("guardian_cpf"):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -v`
Expected: PASS for the new test, and no regressions in
`test_collect_info_guardian_name_also_sets_user_name`,
`test_collect_info_adult_skips_guardian_steps`,
`test_collect_info_asks_guardian_cpf_after_guardian_name`,
`test_collect_info_returning_minor_guardian_name_skips_guardian_cpf`,
`test_collect_info_new_minor_after_guardian_cpf_goes_to_doctor` (all use
`is_patient=False`, so unaffected).

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "fix(nodes): skip guardian name/CPF steps for self-messaging minors

Steps 6/7 of collect_info_node now only require guardian_name/
guardian_cpf when a third party (is_patient=False) is messaging.
This was the actual deadlock: these steps fire on every turn
regardless of what _next_question previews, so a self-messaging
minor (is_patient=True, no guardian contact) could never get past
them — see the Clara case, 5581999249242, 2026-07-21."
```

---

### Task 4: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --tb=short`
Expected: all tests PASS, no failures introduced by Tasks 1-3.

- [ ] **Step 2: Confirm no other guardian-requirement call site was missed**

Run: `grep -n "guardian_name\|guardian_cpf\|guardian_relationship" app/database.py app/graph/nodes.py`
Expected: every remaining reference is either (a) one of the three now-guarded checks fixed in Tasks 1-3, (b) a display/prompt-injection read with a "não informado" fallback (app/graph/nodes.py ~1462-1468, app/graph/tools.py ~48-53), or (c) the `_STATE_TO_DB` mapping / persistence plumbing in `_extract_and_ask` and `upsert_user` — none of which need changes per the design doc's "Fora de escopo" section.

- [ ] **Step 3: Commit if Step 2 turned up nothing to fix**

No commit needed if Step 1 and Step 2 raise no issues — Tasks 1-3 already committed their own changes.
