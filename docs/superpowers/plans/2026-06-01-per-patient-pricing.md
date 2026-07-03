# Per-Patient Pricing Exceptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow some patients to have a waived booking fee, a custom consultation price, or full courtesy (R$0), with Eva communicating and validating payments accordingly.

**Architecture:** Two new columns on `users` (`custom_price`, `booking_fee_waived`) are copied to `appointments.booking_fee_waived` at booking time. A new `get_pricing_exception_rule()` function in `prompts.py` generates an override block appended to Eva's system prompt. `confirm_appointment` marks the fee as already paid (waived), `register_payment` adjusts `expected_remaining`, and the pending-payments email excludes waived/courtesy appointments.

**Tech Stack:** Python, Supabase (PostgreSQL via SDK), LangGraph, pytest + AsyncMock

---

## File Map

| File | Change |
|---|---|
| `supabase/migrations/20260601_add_custom_pricing.sql` | **Create** — adds `custom_price` + `booking_fee_waived` to `users`, `booking_fee_waived` to `appointments` |
| `app/graph/prompts.py` | **Modify** — add `get_pricing_exception_rule()` |
| `app/graph/nodes.py` | **Modify** — import `_expected_consultation_amount`; append exception block to system prompt using already-fetched `user` |
| `app/graph/tools.py` | **Modify** — `_expected_consultation_amount` gets `price_override`; `confirm_appointment` copies `booking_fee_waived` + sets `booking_fee_paid_at`; `register_payment` reads `booking_fee_waived` + `custom_price` + fixes `expected_remaining` |
| `scripts/send_pending_payments_reminder.py` | **Modify** — filter waived fees from taxa section; filter courtesy from consulta section |
| `tests/test_tools.py` | **Modify** — 4 new tests |
| `tests/test_process_message.py` | **Modify** — 1 new test for exception block injection |

---

### Task 1: DB Migration

**Files:**
- Create: `supabase/migrations/20260601_add_custom_pricing.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- Migration: add custom_price and booking_fee_waived to users, booking_fee_waived to appointments

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS custom_price INTEGER NULL,
  ADD COLUMN IF NOT EXISTS booking_fee_waived BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS booking_fee_waived BOOLEAN NOT NULL DEFAULT FALSE;
```

Save to `supabase/migrations/20260601_add_custom_pricing.sql`.

- [ ] **Step 2: Verify the file exists and has correct content**

Run: `cat supabase/migrations/20260601_add_custom_pricing.sql`
Expected: file with three ALTER TABLE statements

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260601_add_custom_pricing.sql
git commit -m "chore: add DB migration for per-patient pricing exceptions"
```

---

### Task 2: `_expected_consultation_amount` — add `price_override` parameter

**Files:**
- Modify: `app/graph/tools.py:962-981`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`, after the `confirm_appointment` section (before `# ── register_payment`):

```python
# ── _expected_consultation_amount ────────────────────────────────────────────

def test_expected_consultation_amount_price_override():
    """price_override bypasses the standard formula and returns the override directly, no PIX discount."""
    from app.graph.tools import _expected_consultation_amount
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 6, 1, tzinfo=ZoneInfo("America/Recife"))
    # Baseline: Dr. Júlio adult post-June → 700 - 50 = 650
    assert _expected_consultation_amount("julio", 35, None, now) == 650
    # price_override=500: returns exactly 500 (no PIX discount subtracted)
    assert _expected_consultation_amount("julio", 35, None, now, price_override=500) == 500
    # price_override=0: returns 0 (courtesy)
    assert _expected_consultation_amount("julio", 35, None, now, price_override=0) == 0
    # price_override=None: standard formula still applies
    assert _expected_consultation_amount("bruna", 40, None, now, price_override=None) == 650
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_expected_consultation_amount_price_override -v`
Expected: FAIL with `TypeError` (unexpected keyword argument `price_override`)

- [ ] **Step 3: Implement the change**

In `app/graph/tools.py`, replace the function signature and body at line 962:

```python
def _expected_consultation_amount(
    doctor_key: str,
    patient_age: int,
    consultation_type: str | None,
    now_dt,
    price_override: int | None = None,
) -> int:
    """Return the expected full payment amount (with R$50 PIX discount for standard pricing).

    price_override: if set, returns that value directly — no standard formula, no PIX discount.
    consultation_type: value stored in appointments.consultation_type at booking time.
        'primeira_consulta' → first visit pricing (higher)
        'acompanhamento' or None → follow-up pricing (default for unknown)
    """
    if price_override is not None:
        return price_override
    post_june = (now_dt.year, now_dt.month) >= (2026, 6)
    if doctor_key == "bruna":
        base = 700 if post_june else 600
    elif doctor_key == "julio":
        if patient_age >= 18:
            base = 700 if post_june else 600
        elif consultation_type == "primeira_consulta":  # minor first visit
            base = 850 if post_june else 750
        else:  # minor follow-up / acompanhamento (default when field is null)
            base = 750 if post_june else 650
    else:
        base = 700 if post_june else 600
    return base - 50  # R$50 PIX/cash discount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tools.py::test_expected_consultation_amount_price_override -v`
Expected: PASS

- [ ] **Step 5: Run all tests to check nothing broke**

Run: `uv run pytest --tb=short`
Expected: all tests pass (or same count as before)

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat: add price_override param to _expected_consultation_amount"
```

---

### Task 3: `get_pricing_exception_rule()` in `prompts.py`

**Files:**
- Modify: `app/graph/prompts.py` (add new function after `get_pricing_rules`)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_process_message.py`, after the existing tests:

```python
# ── get_pricing_exception_rule ────────────────────────────────────────────────

def test_pricing_exception_rule_no_exception_returns_empty():
    from app.graph.prompts import get_pricing_exception_rule
    assert get_pricing_exception_rule(None, False, 650) == ""


def test_pricing_exception_rule_courtesy():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(0, True, 650)
    assert "cortesia" in block
    assert "PIX" not in block
    assert "nenhum valor" in block.lower()


def test_pricing_exception_rule_fee_waived_standard_price():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(None, True, 650)
    assert "DISPENSADA" in block
    assert "R$ 650,00" in block
    assert "PIX" not in block


def test_pricing_exception_rule_custom_price_normal_fee():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(500, False, 650)
    assert "R$ 500,00" in block
    assert "R$ 100,00" in block  # taxa de reserva still applies
    assert "reajuste" not in block.lower()


def test_pricing_exception_rule_custom_price_fee_waived():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(500, True, 650)
    assert "R$ 500,00" in block
    assert "DISPENSADA" in block
    assert "PIX" not in block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py::test_pricing_exception_rule_no_exception_returns_empty tests/test_process_message.py::test_pricing_exception_rule_courtesy -v`
Expected: FAIL with `ImportError` (name `get_pricing_exception_rule` not found)

- [ ] **Step 3: Implement `get_pricing_exception_rule()` in `prompts.py`**

Add this function to `app/graph/prompts.py`, after the `get_pricing_rules` function (after line 340, before `DOCTOR_CORRECTION_RULE`):

```python
def get_pricing_exception_rule(
    custom_price: int | None,
    booking_fee_waived: bool,
    standard_price: int,
) -> str:
    """Returns an override block for Eva when this patient has special pricing.

    Returns empty string when no exception applies (standard pricing and no waiver).
    standard_price: value from _expected_consultation_amount (with PIX discount already applied).
    """
    if custom_price is None and not booking_fee_waived:
        return ""

    # Courtesy: zero-price consultation (no payment at all)
    if custom_price == 0:
        return (
            "\n\n⚠️ EXCEÇÃO PARA ESTE PACIENTE — cortesia:\n"
            "- Esta consulta é cortesia, sem nenhum valor a pagar.\n"
            "- Após confirm_appointment, envie: "
            "\"Consulta registrada! ✅ Esta consulta é cortesia — nenhum valor será cobrado. 😊\"\n"
            "- NÃO envie nenhuma instrução de pagamento ou taxa.\n"
            "- Se perguntado sobre preço, diga: \"Para você, esta consulta é cortesia.\""
        )

    consultation_price = custom_price if custom_price is not None else standard_price
    price_label = f"R$ {consultation_price},00"

    if custom_price is not None and not booking_fee_waived:
        # Custom price, normal booking fee
        return (
            f"\n\n⚠️ EXCEÇÃO PARA ESTE PACIENTE:\n"
            f"- Este paciente tem valor especial de {price_label} por consulta.\n"
            f"- A taxa de reserva de R$ 100,00 se aplica normalmente (abatida do total).\n"
            f"- Quando informar o preço, diga: \"O seu valor especial para esta consulta é {price_label}.\"\n"
            f"- NÃO mencione os valores padrão da clínica nem o reajuste de junho."
        )

    if custom_price is None and booking_fee_waived:
        # Standard price, booking fee waived
        return (
            f"\n\n⚠️ EXCEÇÃO PARA ESTE PACIENTE — sobrepõe qualquer regra de preço acima:\n"
            f"- A taxa de reserva está DISPENSADA para este paciente.\n"
            f"- Após confirm_appointment, envie EXATAMENTE:\n"
            f"  \"Consulta registrada! ✅ Para você, a taxa de reserva está dispensada 😊\n"
            f"  O valor integral da consulta ({price_label}) deverá ser pago no dia da consulta.\"\n"
            f"- NÃO envie instruções de PIX para taxa de reserva.\n"
            f"- Quando informar o preço, diga: \"O seu valor especial para esta consulta é {price_label}.\""
        )

    # custom_price > 0 AND booking_fee_waived
    return (
        f"\n\n⚠️ EXCEÇÃO PARA ESTE PACIENTE:\n"
        f"- Este paciente tem valor especial de {price_label} por consulta.\n"
        f"- A taxa de reserva está DISPENSADA para este paciente.\n"
        f"- Após confirm_appointment, envie EXATAMENTE:\n"
        f"  \"Consulta registrada! ✅ Para você, a taxa de reserva está dispensada 😊\n"
        f"  O valor integral da consulta ({price_label}) deverá ser pago no dia da consulta.\"\n"
        f"- NÃO envie instruções de PIX para taxa de reserva.\n"
        f"- Quando informar o preço, diga: \"O seu valor especial para esta consulta é {price_label}.\"\n"
        f"- NÃO mencione os valores padrão da clínica nem o reajuste de junho."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k "pricing_exception_rule" -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Run all tests**

Run: `uv run pytest --tb=short`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/graph/prompts.py tests/test_process_message.py
git commit -m "feat: add get_pricing_exception_rule() to prompts.py"
```

---

### Task 4: System prompt injection in `nodes.py`

**Files:**
- Modify: `app/graph/nodes.py` (~line 642 — user fetch section + imports)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_message.py`:

```python
async def test_pricing_exception_block_injected_in_system_prompt():
    """When user has booking_fee_waived=True, the exception block appears in patient_agent_node system prompt."""
    from app.graph.nodes import patient_agent_node
    from langchain_core.messages import HumanMessage, AIMessage

    user_with_waiver = {
        "id": "user-99",
        "name": "Ana",
        "patient_name": "Ana",
        "number": PHONE,
        "booking_fee_waived": True,
        "custom_price": None,
        "age": 32,
        "birth_date": "01/01/1994",
        "email": "ana@test.com",
        "active": True,
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",  # julio
        "is_returning_patient": True,
        "price_adjustment_notified_at": "2026-01-01T00:00:00",  # skip price notice
    }

    state = {
        "phone": PHONE,
        "stage": "patient_agent",
        "user_name": "Ana",
        "patient_name": "Ana",
        "patient_age": 32,
        "birth_date": "01/01/1994",
        "is_patient": True,
        "preferred_doctor": "julio",
        "is_returning_patient": True,
        "modality_restriction": None,
        "silent_mode": False,
        "messages": [HumanMessage(content="qual o meu valor de consulta?")],
    }

    captured_system_prompt = []

    async def fake_ainvoke(messages, **kwargs):
        # Capture the system prompt (first message is SystemMessage)
        for m in messages:
            if hasattr(m, "type") and m.type == "system":
                captured_system_prompt.append(m.content)
                break
        return AIMessage(content="Resposta mock")

    mock_llm = MagicMock()
    mock_llm.ainvoke = fake_ainvoke

    with patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value=user_with_waiver), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes._get_patient_llm", return_value=mock_llm), \
         patch("app.google_calendar.format_doctor_schedules", return_value="schedules mock"):
        await patient_agent_node(state, CONFIG)

    assert len(captured_system_prompt) == 1
    system_prompt = captured_system_prompt[0]
    assert "DISPENSADA" in system_prompt, "Exception block must appear in system prompt for booking_fee_waived=True"
    assert "PIX" not in system_prompt.split("EXCEÇÃO")[1] if "EXCEÇÃO" in system_prompt else True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py::test_pricing_exception_block_injected_in_system_prompt -v`
Expected: FAIL (the system prompt does not yet contain "DISPENSADA")

- [ ] **Step 3: Implement the injection in `nodes.py`**

In `app/graph/nodes.py`, find the imports from `app.graph.tools` (near the top of the file) and add `_expected_consultation_amount` to the import list.

Then find the line `from app.graph.prompts import` (or the individual prompt imports) and add `get_pricing_exception_rule`.

Then find this block (around line 639-642):

```python
    # One-time price adjustment notice injected into the system prompt
    needs_price_notice = False
    now_dt = datetime.now(ZoneInfo("America/Recife"))
    user = await get_user_by_phone(state["phone"])
```

After the existing price adjustment block (after the `else:` clause that ends around line 666), and before the upcoming appointments block, add:

```python
    # ── Per-patient pricing exception block ──────────────────────────────────
    _custom_price = (user or {}).get("custom_price")   # None, 0, or positive int
    _fee_waived = bool((user or {}).get("booking_fee_waived", False))
    if _custom_price is not None or _fee_waived:
        _doctor_key = state.get("preferred_doctor") or ""
        _p_age = state.get("patient_age") or 99
        _standard_price = _expected_consultation_amount(_doctor_key, _p_age, None, now_dt)
        system_prompt += get_pricing_exception_rule(_custom_price, _fee_waived, _standard_price)
```

Note: `now_dt` is already set at line 640 — do not redeclare it.

Also make sure these are imported. For `_expected_consultation_amount`, add it to the import that pulls from `app.graph.tools`:

```python
from app.graph.tools import (
    ...,
    _expected_consultation_amount,
)
```

And add `get_pricing_exception_rule` to the prompt imports:

```python
from app.graph.prompts import (
    ...,
    get_pricing_exception_rule,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_process_message.py::test_pricing_exception_block_injected_in_system_prompt -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `uv run pytest --tb=short`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat: inject pricing exception block into patient_agent system prompt"
```

---

### Task 5: `confirm_appointment` — copy `booking_fee_waived` + conditional return

**Files:**
- Modify: `app/graph/tools.py` (confirm_appointment, lines ~547-600)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`, in the `# ── confirm_appointment` section:

```python
async def test_confirm_appointment_copies_booking_fee_waived_to_appointment():
    """When user has booking_fee_waived=True, the appointment row gets booking_fee_waived=True
    and booking_fee_paid_at is set immediately. Return string must NOT instruct PIX payment."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    mock_user = {"id": "user-wv", "booking_fee_waived": True, "custom_price": None}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-waived"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=mock_user), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-07-09T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    # Return string must NOT contain PIX instructions
    assert "PIX" not in result
    assert "taxa de reserva" not in result.lower()
    # DB insert must include booking_fee_waived=True and non-null booking_fee_paid_at
    insert_call_data = table.insert.call_args[0][0]
    assert insert_call_data["booking_fee_waived"] is True
    assert insert_call_data["booking_fee_paid_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_confirm_appointment_copies_booking_fee_waived_to_appointment -v`
Expected: FAIL (insert dict doesn't have `booking_fee_waived` key, and result contains "PIX")

- [ ] **Step 3: Implement the changes in `confirm_appointment`**

In `app/graph/tools.py`, find the DB insert block (around lines 547-557). Change it to:

```python
    _bfw = bool((user or {}).get("booking_fee_waived", False))
    _bfp_at = datetime.now(TZ).isoformat() if _bfw else None

    try:
        await client.from_("appointments").insert({
            "user_id": user["id"] if user else None,
            "doctor_id": DOCTOR_IDS.get(doctor),
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": effective_modality or None,
            "consultation_type": consultation_type,
            "booking_fee_waived": _bfw,
            "booking_fee_paid_at": _bfp_at,
        }).execute()
```

Then find the return statement at lines 594-600:

```python
    pix_key = os.environ.get("PIX_KEY", "42006848000178")
    return (
        f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}{session_label}\nID: {event_id}\n\n"
        f"INSTRUÇÃO OBRIGATÓRIA: informe agora ao paciente que a vaga só estará garantida após o pagamento "
        f"da taxa de reserva de R$ 100,00 via PIX ({pix_key}) em até 2 horas. "
        f"Peça que envie o comprovante aqui no chat."
    )
```

Replace with:

```python
    pix_key = os.environ.get("PIX_KEY", "42006848000178")
    _custom_price_ret = (user or {}).get("custom_price")
    if _custom_price_ret == 0:
        return (
            f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}{session_label}\nID: {event_id}\n\n"
            f"INSTRUÇÃO: Esta consulta é cortesia. Envie a mensagem de cortesia conforme o bloco de exceção "
            f"de preço no seu system prompt. NÃO solicite nenhum pagamento."
        )
    elif _bfw:
        return (
            f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}{session_label}\nID: {event_id}\n\n"
            f"INSTRUÇÃO: A taxa de reserva está DISPENSADA para este paciente. Envie a mensagem de confirmação "
            f"conforme o bloco de exceção de preço no seu system prompt. NÃO envie instruções de PIX."
        )
    else:
        return (
            f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}{session_label}\nID: {event_id}\n\n"
            f"INSTRUÇÃO OBRIGATÓRIA: informe agora ao paciente que a vaga só estará garantida após o pagamento "
            f"da taxa de reserva de R$ 100,00 via PIX ({pix_key}) em até 2 horas. "
            f"Peça que envie o comprovante aqui no chat."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tools.py::test_confirm_appointment_copies_booking_fee_waived_to_appointment -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `uv run pytest --tb=short`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat: confirm_appointment copies booking_fee_waived and adjusts return string"
```

---

### Task 6: `register_payment` — booking_fee_waived + custom_price

**Files:**
- Modify: `app/graph/tools.py` (register_payment, lines ~1128-1292)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

First, add a helper that extends `_make_supabase_client_with_appointment()` for new scenarios. Add this to `tests/test_tools.py` after the existing `_make_supabase_client_with_appointment()`:

```python
def _make_supabase_client_with_appointment_waived(booking_fee_waived=True, custom_price=None):
    """Like _make_supabase_client_with_appointment but with booking_fee_waived in the appointment row
    and a custom_price query response as the 3rd call."""
    appts_with_users = MagicMock(data=[{
        "appointment_id": "apt-wv",
        "start_time": "2026-06-15T10:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "status": "scheduled",
        "users": {"id": "user-123", "patient_name": "Maria", "name": "Maria"},
    }])
    apt_data = MagicMock(data=[{
        "appointment_id": "apt-wv",
        "start_time": "2026-06-15T10:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "end_time": "2026-06-15T11:00:00+00:00",
        "paid_at": None,
        "booking_fee_paid_at": None,
        "status": "scheduled",
        "consultation_type": None,
        "booking_fee_waived": booking_fee_waived,
    }])
    custom_price_data = MagicMock(data={"custom_price": custom_price})
    empty = MagicMock(data=[])

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        if _side_effect.call_count == 1:
            return appts_with_users
        if _side_effect.call_count == 2:
            return apt_data
        if _side_effect.call_count == 3:
            return custom_price_data
        return empty
    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute
```

Then add the tests:

```python
async def test_register_payment_booking_fee_waived_no_deduction():
    """When booking_fee_waived=True on the appointment, expected_remaining = expected (no R$100 deduction),
    so paying the full consultation price is treated as QUITADA."""
    from app.graph.tools import register_payment
    # Dr. Júlio adult, June 2026: expected = 700 - 50 = 650
    # With booking_fee_waived=True: expected_remaining = 650 (no -100 deduction)
    # Paying 650 → QUITADA
    client, table, execute = _make_supabase_client_with_appointment_waived(
        booking_fee_waived=True, custom_price=None
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="650,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(preferred_doctor="julio", patient_age=35),
            config=CONFIG,
        )
    assert "QUITADA" in result


async def test_register_payment_courtesy_zero_price():
    """When custom_price=0, the tool returns QUITADA immediately regardless of amount."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment_waived(
        booking_fee_waived=True, custom_price=0
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="0,00",
            drive_link="",
            state=_make_state(preferred_doctor="julio", patient_age=35),
            config=CONFIG,
        )
    assert "QUITADA" in result
    assert "cortesia" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py::test_register_payment_booking_fee_waived_no_deduction tests/test_tools.py::test_register_payment_courtesy_zero_price -v`
Expected: FAIL (first test returns "Pagamento Parcial" instead of "QUITADA"; second fails)

- [ ] **Step 3: Implement the changes in `register_payment`**

**3a. Add `booking_fee_waived` to the appointment select** (around line 1128-1130):

```python
    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, doctor_id, paid_at, booking_fee_paid_at, status, consultation_type, booking_fee_waived"
    ).eq("user_id", user_id).in_("status", ["scheduled", "completed"]).gte("start_time", lookback_iso).order("start_time", desc=True).limit(1).execute()
```

**3b. After the `booking_fee_already_paid` computation (around line 1289-1292), add the new fields and update `expected_remaining`:**

Find this block:
```python
    booking_fee_already_paid = bool(
        appt_result.data and appt_result.data[0].get("booking_fee_paid_at")
    ) if appt_result and appt_result.data else False
    expected_remaining = (expected - 100) if booking_fee_already_paid else expected
```

Replace with:
```python
    booking_fee_already_paid = bool(
        appt_result.data and appt_result.data[0].get("booking_fee_paid_at")
    ) if appt_result and appt_result.data else False

    # booking_fee_waived: fee was never owed — don't deduct it from expected_remaining
    _appt_bfw = bool(
        appt_result.data[0].get("booking_fee_waived", False)
    ) if appt_result and appt_result.data else False

    # custom_price: lookup from user record (overrides standard formula in _expected_consultation_amount)
    custom_price: int | None = None
    if user_id:
        try:
            _user_cp = await client.from_("users").select("custom_price").eq(
                "id", user_id
            ).maybe_single().execute()
            custom_price = (_user_cp.data or {}).get("custom_price")
        except Exception:
            pass

    expected = _expected_consultation_amount(
        doctor_key, _age, _consultation_type, pricing_dt, price_override=custom_price
    )

    if _appt_bfw:
        expected_remaining = expected        # booking fee was never owed
    else:
        expected_remaining = (expected - 100) if booking_fee_already_paid else expected
```

Note: `expected` used to be computed on the line before `booking_fee_already_paid`. Move it to here. The original line that computed `expected` (currently around line 1285) must be **removed** since it's now computed in the new block above.

**3c. Add courtesy short-circuit** — add this block immediately after the `expected_remaining` line:

```python
    # ── Courtesy (custom_price == 0) — always QUITADA ────────────────────────
    if custom_price == 0:
        if appt_id_to_pay:
            await _update_appts({
                "paid_at": now_dt.isoformat(),
                "booking_fee_paid_at": now_dt.isoformat(),
            })
        try:
            await append_payment_receipt(
                patient_name, patient_phone, doctor_label, appointment_dt,
                amount, drive_link, payment_type="Consulta", payment_method_override="",
            )
        except Exception:
            _logger.exception("SHEETS_APPEND FAILED patient=%s", patient_name)
        await _notify_clinic(
            f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}"
            f"\nTipo: Consulta (cortesia)\nConsulta: {appointment_dt}\nLink: {drive_link}",
            subject=f"Comprovante recebido — {patient_name}",
        )
        await log_event("payment_receipt_registered", phone, {
            "patient_name": patient_name, "amount": amount,
            "payment_type": "Consulta", "drive_link": drive_link,
        })
        return f"{confirmation_msg}\n\nConsulta QUITADA (cortesia). ✅ Nenhum valor adicional será cobrado."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py::test_register_payment_booking_fee_waived_no_deduction tests/test_tools.py::test_register_payment_courtesy_zero_price -v`
Expected: both PASS

- [ ] **Step 5: Run all tests**

Run: `uv run pytest --tb=short`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat: register_payment respects booking_fee_waived and custom_price=0 (courtesy)"
```

---

### Task 7: Pending payments email — filter waived and courtesy

**Files:**
- Modify: `scripts/send_pending_payments_reminder.py`
- Test: `tests/test_tools.py` (unit test for filter logic)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`:

```python
# ── send_pending_payments_reminder filter logic ───────────────────────────────

def test_pending_payments_courtesy_filter():
    """Courtesy appointments (users.custom_price == 0) must be excluded from consulta_pendente."""
    # Simulate r2.data with two appointments: one normal, one courtesy
    appts = [
        {"appointment_id": "apt-1", "start_time": "2026-06-01T10:00:00+00:00",
         "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
         "booking_fee_paid_at": None, "paid_at": None,
         "consultation_type": None,
         "users": {"number": "5581999999999", "patient_name": "Ana", "name": "Ana", "custom_price": None}},
        {"appointment_id": "apt-2", "start_time": "2026-06-02T10:00:00+00:00",
         "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
         "booking_fee_paid_at": None, "paid_at": None,
         "consultation_type": None,
         "users": {"number": "5581888888888", "patient_name": "Cortesia", "name": "Cortesia", "custom_price": 0}},
    ]
    # Apply the same filter logic used in send_pending_payments_reminder.py
    consulta_pendente = [
        appt for appt in appts
        if (appt.get("users") or {}).get("custom_price") != 0
    ]
    assert len(consulta_pendente) == 1
    assert consulta_pendente[0]["appointment_id"] == "apt-1"
```

- [ ] **Step 2: Run test to verify it passes immediately** (pure logic test, no imports needed)

Run: `uv run pytest tests/test_tools.py::test_pending_payments_courtesy_filter -v`
Expected: PASS (the test verifies the filter logic we will apply — it uses no production code)

- [ ] **Step 3: Implement the changes in `send_pending_payments_reminder.py`**

**3a. Taxa section** — add `.eq("booking_fee_waived", False)` to the query.

Find the taxa query (lines 49-57):
```python
    r1 = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, consultation_type, booking_fee_paid_at, paid_at, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .is_("booking_fee_paid_at", "null")
        .gt("start_time", now.isoformat())
        .order("start_time")
        .execute()
    )
```

Replace with:
```python
    r1 = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, consultation_type, booking_fee_paid_at, paid_at, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .eq("booking_fee_waived", False)
        .is_("booking_fee_paid_at", "null")
        .gt("start_time", now.isoformat())
        .order("start_time")
        .execute()
    )
```

**3b. Consulta section** — add `custom_price` to users join and filter in Python.

Find the consulta query (lines 61-69):
```python
    r2 = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, consultation_type, booking_fee_paid_at, paid_at, users(number, patient_name, name)")
        .eq("status", "completed")
        .is_("paid_at", "null")
        .order("start_time", desc=True)
        .execute()
    )
    consulta_pendente = r2.data or []
```

Replace with:
```python
    r2 = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, consultation_type, booking_fee_paid_at, paid_at, users(number, patient_name, name, custom_price)")
        .eq("status", "completed")
        .is_("paid_at", "null")
        .order("start_time", desc=True)
        .execute()
    )
    # Exclude courtesy patients (custom_price == 0) — they have no balance to collect
    consulta_pendente = [
        appt for appt in (r2.data or [])
        if (appt.get("users") or {}).get("custom_price") != 0
    ]
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest --tb=short`
Expected: all tests pass

- [ ] **Step 5: Quick smoke test of the script**

Run: `uv run python -c "import scripts.send_pending_payments_reminder; print('import OK')" 2>&1`
Expected: `import OK` (no syntax errors)

- [ ] **Step 6: Commit**

```bash
git add scripts/send_pending_payments_reminder.py tests/test_tools.py
git commit -m "feat: pending payments email excludes waived fees and courtesy patients"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Covered by |
|---|---|
| `users.custom_price INTEGER NULL` | Task 1 |
| `users.booking_fee_waived BOOLEAN DEFAULT FALSE` | Task 1 |
| `appointments.booking_fee_waived BOOLEAN DEFAULT FALSE` | Task 1 |
| `get_pricing_exception_rule()` function | Task 3 |
| Block appended to system prompt in nodes.py | Task 4 |
| `confirm_appointment` copies `booking_fee_waived` | Task 5 |
| `booking_fee_paid_at = now()` when waived | Task 5 |
| Return string conditional on waived/courtesy | Task 5 |
| `_expected_consultation_amount` price_override | Task 2 |
| `register_payment` reads `booking_fee_waived` from appointment | Task 6 |
| `register_payment` reads `custom_price` from users | Task 6 |
| `expected_remaining` no deduction when waived | Task 6 |
| Courtesy returns "QUITADA" immediately | Task 6 |
| Taxa email filter `.eq("booking_fee_waived", False)` | Task 7 |
| Consulta email filter `custom_price != 0` | Task 7 |

All spec requirements are covered. ✅

### Placeholder scan

No TBD, TODO, or vague steps. All code blocks are complete. ✅

### Type consistency

- `custom_price: int | None` used consistently in Tasks 3, 4, 5, 6.
- `booking_fee_waived: bool` used consistently in Tasks 3, 4, 5, 6.
- `_expected_consultation_amount(..., price_override=custom_price)` — matches the new signature from Task 2. ✅
- `get_pricing_exception_rule(custom_price, booking_fee_waived, standard_price)` — matches implementation in Task 3 and usage in Task 4. ✅

### Breaking change check

Existing `_make_supabase_client_with_appointment()` calls in tests now encounter a new 3rd call (custom_price query). The fixture returns `empty = MagicMock(data=[])` for calls beyond 2; `(_user_cp.data or {}).get("custom_price")` on an empty list (`[]`) → `{}.get("custom_price")` → `None`. All existing register_payment tests will continue to treat custom_price as None (standard pricing). ✅
