# External Contact Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement tools for registering and tracking requests when patients/guardians ask doctors to contact external third parties (psychologists, therapists, etc.) before consultations, with email notifications to doctors on initial request and follow-ups.

**Architecture:** New table `requests` in Supabase with shared columns (phone, patient_name, doctor_id, content) + type-specific metadata. Two tools (`request_external_contact` for initial requests, `nudge_external_contact` for follow-ups). Two email helpers send notifications to doctors. Tools are registered in LangGraph and instructed in prompts.

**Tech Stack:** Python async/await, Supabase (PostgreSQL), SMTP email, LangGraph tool decorators, pytest with mocks.

---

## File Structure

**Create:**
- `supabase/migrations/20260721_create_requests_table.sql` — table `requests`
- `tests/test_external_contact.py` — unit tests for both tools

**Modify:**
- `app/email_sender.py` — add `send_external_contact_request_email()`, `send_external_contact_nudge_email()`
- `app/graph/tools.py` — add `request_external_contact()`, `nudge_external_contact()`
- `app/graph/nodes.py` — register both tools in `TOOLS` list
- `app/graph/prompts.py` — add instruction blocks (two places: ~lines 780-824 and ~1090-1114)

---

## Task 1: Create database migration

**Files:**
- Create: `supabase/migrations/20260721_create_requests_table.sql`

- [ ] **Step 1: Write the migration file**

```sql
-- Create requests table for generic request tracking
-- Shared columns (phone, patient_name, doctor_id, content) apply to all types
-- Type-specific fields live in metadata JSONB

create table requests (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  type text not null,                    -- "contato_terceiro", extensible
  phone text not null,                   -- WhatsApp phone, every request has one
  patient_name text,                     -- patient being discussed, nullable
  doctor_id uuid,                        -- responsible doctor, nullable
  content text not null,                 -- human-readable summary of request
  metadata jsonb not null default '{}'   -- type-specific fields
);

alter table requests enable row level security;

-- No policies: backend uses service_role, ignores RLS
-- This closes the door for future accidental use of anon key
```

Save to `supabase/migrations/20260721_create_requests_table.sql`.

- [ ] **Step 2: Verify migration file is readable**

Run: `head -5 supabase/migrations/20260721_create_requests_table.sql`
Expected: First 5 lines visible, SQL looks syntactically correct

- [ ] **Step 3: Commit migration**

```bash
git add supabase/migrations/20260721_create_requests_table.sql
git commit -m "db: create requests table for generic request tracking"
```

---

## Task 2: Write tests for request_external_contact

**Files:**
- Create: `tests/test_external_contact.py`
- Reference: `tests/test_tools.py` (for mock patterns)

- [ ] **Step 1: Read existing test patterns from test_tools.py**

Run: `grep -A 20 "def test_request_document" tests/test_tools.py | head -30`

This shows how mocks are structured. Capture patterns for:
- How state dict is built
- How config with "phone" is mocked
- How Supabase client mock works
- How to assert on log_event calls

- [ ] **Step 2: Write test_external_contact.py with full test suite**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.graph.tools import request_external_contact
from app.database import DOCTOR_IDS


@pytest.mark.asyncio
async def test_request_external_contact_success():
    """Test successful external contact request registration and email sending."""
    
    # Mock state and config
    state = {
        "patient_name": "Suzi Monteiro Viana",
        "patient_age": 17,
        "user_db_id": "user-123",
        "preferred_doctor": "julio",
        "patient_email": "suzi@example.com",
    }
    config = {"configurable": {"phone": "5581996962165@s.whatsapp.net"}}
    
    # Mock Supabase client
    mock_client = AsyncMock()
    mock_doctors_query = AsyncMock()
    mock_doctors_query.single.return_value.data = {
        "agenda_id": "dr.juliogouveia@gmail.com"
    }
    mock_client.from_.return_value.select.return_value.eq.return_value = (
        mock_doctors_query
    )
    
    # Mock requests insert
    mock_requests_query = AsyncMock()
    mock_client.from_.return_value.insert.return_value = mock_requests_query
    
    # Mock get_supabase
    with patch("app.graph.tools.get_supabase", return_value=mock_client):
        # Mock send_external_contact_request_email
        with patch("app.graph.tools.send_external_contact_request_email", new_callable=AsyncMock) as mock_email:
            # Mock log_event
            with patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
                # Mock _notify_clinic
                with patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
                    result = await request_external_contact(
                        third_party_role="psicóloga",
                        third_party_name="Bruna Psicóloga",
                        reason="acompanhamento antes da consulta de 22/07",
                        state=state,
                        config=config,
                        third_party_contact="bruna.psico@example.com",
                    )
    
    # Assertions
    assert "registrada" in result.lower() or "sucesso" in result.lower()
    
    # Verify insert was called
    mock_client.from_.return_value.insert.assert_called_once()
    call_args = mock_client.from_.return_value.insert.call_args
    inserted_data = call_args[0][0] if call_args[0] else call_args[1].get("body", {})
    
    assert inserted_data["type"] == "contato_terceiro"
    assert inserted_data["phone"] == "5581996962165@s.whatsapp.net"
    assert inserted_data["patient_name"] == "Suzi Monteiro Viana"
    assert "psicóloga" in inserted_data["content"].lower()
    
    # Verify metadata has specific fields
    metadata = inserted_data["metadata"]
    assert metadata["third_party_role"] == "psicóloga"
    assert metadata["third_party_name"] == "Bruna Psicóloga"
    assert metadata["third_party_contact"] == "bruna.psico@example.com"
    
    # Verify email was sent
    mock_email.assert_called_once()
    
    # Verify log was called
    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == "external_contact_requested"
    
    # Verify clinic notification was sent
    mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_request_external_contact_missing_patient_name():
    """Test fallback to get_user_by_phone when patient_name not in state."""
    
    state = {
        "preferred_doctor": "julio",
        "patient_email": "test@example.com",
    }
    config = {"configurable": {"phone": "5581996962165@s.whatsapp.net"}}
    
    mock_client = AsyncMock()
    mock_user = {"patient_name": "Fallback Patient"}
    
    with patch("app.graph.tools.get_supabase", return_value=mock_client):
        with patch("app.graph.tools.get_user_by_phone", return_value=mock_user):
            with patch("app.graph.tools.send_external_contact_request_email", new_callable=AsyncMock):
                with patch("app.graph.tools.log_event", new_callable=AsyncMock):
                    with patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
                        # Setup mock queries
                        mock_doctors_query = AsyncMock()
                        mock_doctors_query.single.return_value.data = {
                            "agenda_id": "dr.juliogouveia@gmail.com"
                        }
                        mock_client.from_.return_value.select.return_value.eq.return_value = (
                            mock_doctors_query
                        )
                        mock_requests_query = AsyncMock()
                        mock_client.from_.return_value.insert.return_value = mock_requests_query
                        
                        result = await request_external_contact(
                            third_party_role="terapeuta",
                            third_party_name="João Terapeuta",
                            reason="acompanhamento",
                            state=state,
                            config=config,
                        )
    
    # Verify fallback was used
    call_args = mock_client.from_.return_value.insert.call_args
    inserted_data = call_args[0][0]
    assert inserted_data["patient_name"] == "Fallback Patient"
```

- [ ] **Step 3: Run tests to verify they fail (tools not yet implemented)**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py -v`

Expected: Tests FAIL with `ImportError: cannot import name 'request_external_contact'` or similar (tool doesn't exist yet).

- [ ] **Step 4: Commit test file**

```bash
git add tests/test_external_contact.py
git commit -m "test: add tests for request_external_contact and nudge_external_contact"
```

---

## Task 3: Implement request_external_contact tool

**Files:**
- Modify: `app/graph/tools.py` (add new tool around line 1700, after nudge_doctor_document)

- [ ] **Step 1: Read nudge_doctor_document implementation**

Run: `sed -n '1664,1730p' app/graph/tools.py`

Capture the pattern for: doctor email lookup, log_event call, return value format.

- [ ] **Step 2: Add request_external_contact to tools.py**

Insert after `nudge_doctor_document` function (around line 1730) — find the exact line:

Run: `grep -n "^async def nudge_doctor_document\|^def nudge_doctor_document" app/graph/tools.py | tail -1`

Then add after the closing of that function:

```python
@tool
async def request_external_contact(
    third_party_role: str,
    third_party_name: str,
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    third_party_contact: str = "",
) -> str:
    """Registra um pedido do paciente para que o médico entre em contato com um terceiro
    externo ligado ao cuidado do paciente (psicólogo, terapeuta, outro médico, escola etc.)
    antes ou em torno de uma consulta. Chame na primeira vez que o paciente pedir isso.
    """
    from app.email_sender import send_external_contact_request_email
    from app.database import DOCTOR_IDS, get_user_by_phone

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or ""
    if not patient_name:
        _u = await get_user_by_phone(phone)
        patient_name = (_u or {}).get("patient_name") or (_u or {}).get("name") or "Paciente"

    patient_age = state.get("patient_age")
    patient_email = state.get("patient_email") or ""
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    client = await get_supabase()

    # Fetch doctor email from doctors table
    doctor_email = ""
    if doctor_id:
        result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        doctor_email = result.data.get("agenda_id", "") if result.data else ""

    # Insert into requests table
    await client.from_("requests").insert({
        "type": "contato_terceiro",
        "phone": phone,
        "patient_name": patient_name,
        "doctor_id": doctor_id,
        "content": f"Contato com {third_party_role}: {third_party_name} — {reason}",
        "metadata": {
            "third_party_role": third_party_role,
            "third_party_name": third_party_name,
            "third_party_contact": third_party_contact or None,
        },
    }).execute()

    await log_event("external_contact_requested", phone, {
        "third_party_role": third_party_role,
        "third_party_name": third_party_name,
        "patient_name": patient_name,
    })

    # Send email to doctor
    try:
        await send_external_contact_request_email(
            doctor_key=doctor_key,
            doctor_email=doctor_email,
            patient_name=patient_name,
            patient_age=patient_age,
            phone=phone,
            third_party_role=third_party_role,
            third_party_name=third_party_name,
            third_party_contact=third_party_contact,
            reason=reason,
        )
    except Exception:
        pass

    # Notify clinic
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
    phone_clean = phone.replace("@s.whatsapp.net", "")
    notify_msg = (
        f"📞 Solicitação de contato com terceiro externo\n"
        f"Paciente: {patient_name}\n"
        f"Médico: {doctor_label}\n"
        f"Terceiro: {third_party_role} ({third_party_name})\n"
        f"WhatsApp: {phone_clean}\n"
        f"Motivo: {reason}"
    )
    if third_party_contact:
        notify_msg += f"\nContato do terceiro: {third_party_contact}"
    
    await _notify_clinic(notify_msg, subject=f"Contato com {third_party_role} — {patient_name}")

    return (
        f"Solicitação registrada! ✅\n"
        f"Encaminhamos para o {doctor_label} que entrará em contato com {third_party_name}."
    )
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py::test_request_external_contact_success -v`

Expected: PASS.

- [ ] **Step 4: Run all test_external_contact tests**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_external_contact.py
git commit -m "feat(tools): implement request_external_contact"
```

---

## Task 4: Write tests for nudge_external_contact

**Files:**
- Modify: `tests/test_external_contact.py`

- [ ] **Step 1: Add tests for nudge_external_contact to the file**

Append to `tests/test_external_contact.py`:

```python
@pytest.mark.asyncio
async def test_nudge_external_contact_success():
    """Test nudge when external contact request is pending."""
    
    state = {
        "patient_name": "Suzi Monteiro Viana",
        "patient_age": 17,
        "preferred_doctor": "julio",
        "patient_email": "suzi@example.com",
    }
    config = {"configurable": {"phone": "5581996962165@s.whatsapp.net"}}
    patient_message = "Dr. Júlio ainda não entrou em contato com a psicóloga"
    
    # Mock existing request row
    existing_request = {
        "id": 1,
        "created_at": "2026-07-08T14:00:00Z",
        "type": "contato_terceiro",
        "phone": "5581996962165@s.whatsapp.net",
        "patient_name": "Suzi Monteiro Viana",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "content": "Contato com psicóloga: Bruna — acompanhamento",
        "metadata": {
            "third_party_role": "psicóloga",
            "third_party_name": "Bruna Psicóloga",
            "third_party_contact": "",
        },
    }
    
    mock_client = AsyncMock()
    
    # Mock requests query (find existing)
    mock_requests_query = AsyncMock()
    mock_requests_query.order.return_value.limit.return_value.execute.return_value.data = [
        existing_request
    ]
    mock_client.from_.return_value.select.return_value.eq.return_value.eq.return_value = (
        mock_requests_query
    )
    
    # Mock doctors query (fetch email)
    mock_doctors_query = AsyncMock()
    mock_doctors_query.single.return_value.execute.return_value.data = {
        "agenda_id": "dr.juliogouveia@gmail.com"
    }
    
    with patch("app.graph.tools.get_supabase", return_value=mock_client):
        with patch("app.graph.tools.send_external_contact_nudge_email", new_callable=AsyncMock) as mock_email:
            with patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
                # We need to set up the mock chain for doctors query
                # The first call to from_() is for requests, second for doctors
                from_calls = []
                def from_side_effect(table):
                    if table == "requests":
                        return mock_client.from_.return_value
                    elif table == "doctors":
                        return mock_doctors_query
                    return AsyncMock()
                
                mock_client.from_.side_effect = lambda t: (
                    mock_client.from_.return_value if t == "requests" else mock_doctors_query
                )
                
                result = await nudge_external_contact(
                    patient_message=patient_message,
                    state=state,
                    config=config,
                )
    
    assert "encaminhamos" in result.lower() or "reenviamos" in result.lower()
    mock_email.assert_called_once()
    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == "external_contact_nudge_sent"


@pytest.mark.asyncio
async def test_nudge_external_contact_no_pending_request():
    """Test nudge when no existing external contact request found."""
    
    state = {
        "patient_name": "Patient Name",
        "preferred_doctor": "julio",
    }
    config = {"configurable": {"phone": "5581234567890@s.whatsapp.net"}}
    
    mock_client = AsyncMock()
    
    # Mock empty request list
    mock_requests_query = AsyncMock()
    mock_requests_query.order.return_value.limit.return_value.execute.return_value.data = []
    mock_client.from_.return_value.select.return_value.eq.return_value.eq.return_value = (
        mock_requests_query
    )
    
    with patch("app.graph.tools.get_supabase", return_value=mock_client):
        with patch("app.graph.tools.send_external_contact_nudge_email", new_callable=AsyncMock):
            with patch("app.graph.tools.log_event", new_callable=AsyncMock):
                result = await nudge_external_contact(
                    patient_message="Ainda não falaram com o terceiro",
                    state=state,
                    config=config,
                )
    
    # Should handle gracefully when no request found
    assert result  # Returns something (not crash)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py::test_nudge_external_contact_success -v`

Expected: FAIL with `ImportError: cannot import name 'nudge_external_contact'` (not yet implemented).

- [ ] **Step 3: Commit test updates**

```bash
git add tests/test_external_contact.py
git commit -m "test: add tests for nudge_external_contact"
```

---

## Task 5: Implement nudge_external_contact tool

**Files:**
- Modify: `app/graph/tools.py`

- [ ] **Step 1: Add nudge_external_contact after request_external_contact**

Find where request_external_contact ends (search for the closing brace), then add:

```python
@tool
async def nudge_external_contact(
    patient_message: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Notifica o médico por e-mail quando o paciente cobra sobre um pedido de contato com
    terceiro externo (psicólogo, terapeuta etc.) já registrado anteriormente.
    Chame SOMENTE quando o paciente reforçar/cobrar sobre um pedido já feito
    (ex: 'o Dr. Júlio ainda não falou com a psicóloga'). NÃO use para criar um pedido novo.
    """
    from app.email_sender import send_external_contact_nudge_email
    from app.database import DOCTOR_IDS
    from datetime import datetime, timezone

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
    patient_age = state.get("patient_age")
    patient_email = state.get("patient_email") or ""
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    client = await get_supabase()

    # Find most recent external contact request for this phone
    phone_clean = phone.replace("@s.whatsapp.net", "")
    requests_result = await client.from_("requests").select("*").eq(
        "phone", phone
    ).eq("type", "contato_terceiro").order("created_at", desc=True).limit(1).execute()

    if not requests_result.data:
        # No pending request found
        return (
            "Não encontramos um pedido anterior de contato com terceiro externo. "
            "Você pode fazer um novo pedido se necessário."
        )

    request_row = requests_result.data[0]
    created_at_str = request_row.get("created_at", "data não registrada")
    third_party_name = (request_row.get("metadata") or {}).get("third_party_name", "terceiro")
    third_party_role = (request_row.get("metadata") or {}).get("third_party_role", "profissional")

    # Fetch doctor email
    doctor_email = ""
    if doctor_id:
        res = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        doctor_email = res.data.get("agenda_id", "") if res.data else ""

    try:
        await send_external_contact_nudge_email(
            doctor_key=doctor_key,
            doctor_email=doctor_email,
            patient_name=patient_name,
            patient_age=patient_age,
            phone=phone_clean,
            third_party_role=third_party_role,
            third_party_name=third_party_name,
            patient_message=patient_message,
            requested_at=created_at_str,
        )
    except Exception:
        pass

    await log_event("external_contact_nudge_sent", phone, {
        "third_party_name": third_party_name,
        "patient_message": patient_message,
    })

    return (
        f"Reenviamos a solicitação para o médico. "
        f"Em breve {third_party_name} entrará em contato."
    )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py -v`

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add app/graph/tools.py
git commit -m "feat(tools): implement nudge_external_contact"
```

---

## Task 6: Implement email helpers in app/email_sender.py

**Files:**
- Modify: `app/email_sender.py`

- [ ] **Step 1: Read existing email function patterns**

Run: `sed -n '128,200p' app/email_sender.py`

Capture: SMTP setup, error handling, formatting, constants (DOCTOR_LABELS, etc.).

- [ ] **Step 2: Add send_external_contact_request_email after send_document_nudge_email**

Find the end of `send_document_nudge_email` (around line 140-180) and add:

```python
async def send_external_contact_request_email(
    doctor_key: str,
    doctor_email: str,
    patient_name: str,
    patient_age: int | None,
    phone: str,
    third_party_role: str,
    third_party_name: str,
    third_party_contact: str,
    reason: str,
) -> None:
    """Send an email to the doctor notifying an external contact request.
    Does nothing if SMTP credentials or doctor email are not configured.
    """
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = doctor_email

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    doctor_label = _DOCTOR_LABELS.get(doctor_key, doctor_key)
    phone_clean = phone.replace("@s.whatsapp.net", "")
    age_str = f"{patient_age} anos" if patient_age else "não informada"
    now = datetime.now(TZ).strftime("%d/%m/%Y às %H:%M")

    subject = f"Solicitação de contato — {third_party_role} — {patient_name}"
    body = (
        f"{doctor_label},\n\n"
        f"O(a) paciente {patient_name} ({age_str}) solicitou que você entrasse em contato "
        f"com {third_party_role}: {third_party_name}.\n\n"
        f"Motivo: {reason}\n"
        f"WhatsApp do paciente: {phone_clean}\n"
    )
    if third_party_contact:
        body += f"Contato do {third_party_role}: {third_party_contact}\n"

    body += f"\nSolicitação recebida em: {now}\n\n— Eva, assistente virtual Psique"

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _send_email, smtp_host, smtp_port, smtp_user, smtp_password, to_email, subject, body
    )


async def send_external_contact_nudge_email(
    doctor_key: str,
    doctor_email: str,
    patient_name: str,
    patient_age: int | None,
    phone: str,
    third_party_role: str,
    third_party_name: str,
    patient_message: str,
    requested_at: str,
) -> None:
    """Send a nudge email to doctor when patient follows up on external contact request."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = doctor_email

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    doctor_label = _DOCTOR_LABELS.get(doctor_key, doctor_key)
    phone_clean = phone.replace("@s.whatsapp.net", "")
    age_str = f"{patient_age} anos" if patient_age else "não informada"
    now = datetime.now(TZ).strftime("%d/%m/%Y às %H:%M")

    subject = f"⚠️ Cobrança — contato com {third_party_role} ainda pendente — {patient_name}"
    body = (
        f"{doctor_label},\n\n"
        f"O(a) paciente {patient_name} ({age_str}) segue aguardando contato com "
        f"{third_party_role}: {third_party_name}.\n\n"
        f"Mensagem do paciente: \"{patient_message}\"\n"
        f"WhatsApp: {phone_clean}\n"
        f"Solicitação original: {requested_at}\n"
        f"Reforço enviado em: {now}\n\n"
        f"— Eva, assistente virtual Psique"
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _send_email, smtp_host, smtp_port, smtp_user, smtp_password, to_email, subject, body
    )
```

Make sure to place these after the existing email functions. Check for where `_DOCTOR_LABELS` and `_send_email` are defined (top of file).

- [ ] **Step 3: Run tests to make sure imports work**

Run: `cd /Users/ayexatavares/psique-ai && python -c "from app.email_sender import send_external_contact_request_email, send_external_contact_nudge_email; print('OK')"`

Expected: Output `OK`.

- [ ] **Step 4: Run full test suite for tools**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py -v`

Expected: All tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add app/email_sender.py
git commit -m "feat(email): add external contact request email helpers"
```

---

## Task 7: Register tools in app/graph/nodes.py

**Files:**
- Modify: `app/graph/nodes.py` (lines 10-35 approximately)

- [ ] **Step 1: Read current TOOLS list**

Run: `sed -n '10,35p' app/graph/nodes.py`

Note exact line numbers and tool import/registration pattern.

- [ ] **Step 2: Add imports for new tools**

Find the import section (around line 10-20) and add:

```python
from app.graph.tools import (
    # ... existing imports ...
    request_external_contact,
    nudge_external_contact,
)
```

- [ ] **Step 3: Add tools to TOOLS list**

Find the `TOOLS = [...]` definition (around line 27-35) and add the two new tools:

```python
TOOLS = [
    # ... existing tools ...
    request_external_contact,
    nudge_external_contact,
]
```

- [ ] **Step 4: Verify syntax**

Run: `cd /Users/ayexatavares/psique-ai && python -c "from app.graph.nodes import _get_agent_llm; print('OK')"`

Expected: Output `OK`.

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat(graph): register external contact request tools"
```

---

## Task 8: Add prompt instructions (first block)

**Files:**
- Modify: `app/graph/prompts.py` (around lines 780-824)

- [ ] **Step 1: Read existing document request instructions**

Run: `sed -n '780,825p' app/graph/prompts.py`

Capture the format and structure for when/how to call tools.

- [ ] **Step 2: Find the exact insertion point**

Run: `grep -n "request_document\|nudge_doctor_document" app/graph/prompts.py | head -5`

Look for the block around line 780 that instructs when to call these.

- [ ] **Step 3: Add instructions after the document request block**

Insert after line ~824 (after the nudge_doctor_document instructions):

```
**Contato com terceiro externo (psicólogo, terapeuta, outro médico, escola, etc.):**

Se o paciente pedir que o médico entre em contato com um terceiro externo antes ou em torno da consulta:
- **Primeira vez (novo pedido):** chame `request_external_contact()` com `third_party_role` (ex: "psicóloga"), `third_party_name` (nome do profissional), `reason` (motivo/contexto), e opcionalmente `third_party_contact` (telefone/e-mail do terceiro).
- **Seguinte (paciente cobra):** se o paciente reforçar/cobrar sobre um pedido já feito, chame `nudge_external_contact()` com a `patient_message` (o que ele disse).

NÃO use essas tools para questões clínicas diretas ou dúvidas sobre a própria consulta — elas são só para registrar pedidos de contato com terceiros.
```

- [ ] **Step 4: Test syntax — run the app**

Run: `cd /Users/ayexatavares/psique-ai && python -c "from app.graph.prompts import *; print('OK')"`

Expected: Output `OK`.

- [ ] **Step 5: Commit**

```bash
git add app/graph/prompts.py
git commit -m "docs(prompts): add instructions for external contact request tools (block 1)"
```

---

## Task 9: Add prompt instructions (second block, duplicated)

**Files:**
- Modify: `app/graph/prompts.py` (around lines 1090-1114, second occurrence)

- [ ] **Step 1: Find the second instruction block**

Run: `grep -n "request_document\|nudge_doctor_document" app/graph/prompts.py | tail -5`

This finds the second occurrence around line 1090-1114.

- [ ] **Step 2: Add the same external contact instructions after the second nudge_doctor_document block**

Insert the exact same text as Task 8, Step 3 at the corresponding location in the second block (after line ~1114).

- [ ] **Step 3: Verify both blocks are identical (manual spot check)**

Run: `grep -A 8 "Contato com terceiro externo" app/graph/prompts.py`

Expected: Two identical blocks visible.

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "docs(prompts): add instructions for external contact request tools (block 2)"
```

---

## Task 10: Integration test and full test run

**Files:**
- Reference: `tests/test_tools.py`, `tests/test_external_contact.py`

- [ ] **Step 1: Run full test suite for external contact tools**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/test_external_contact.py -v`

Expected: All tests PASS.

- [ ] **Step 2: Run entire test suite to check for regressions**

Run: `cd /Users/ayexatavares/psique-ai && uv run pytest tests/ -v --tb=short`

Expected: No new failures. Existing tests should still pass.

- [ ] **Step 3: Verify migrations would apply cleanly**

Run: `head -20 supabase/migrations/20260721_create_requests_table.sql`

Expected: SQL looks syntactically valid.

- [ ] **Step 4: Commit final cleanup (if any)**

If no changes, skip. Otherwise:

```bash
git add [any files]
git commit -m "test: verify all tests pass after implementation"
```

---

## Task 11: Quick smoke test of imports and wiring

**Files:**
- Reference: `app/graph/tools.py`, `app/graph/nodes.py`, `app/email_sender.py`, `app/graph/prompts.py`

- [ ] **Step 1: Verify all imports resolve**

Run: `cd /Users/ayexatavares/psique-ai && python << 'EOF'
from app.graph.tools import request_external_contact, nudge_external_contact
from app.email_sender import send_external_contact_request_email, send_external_contact_nudge_email
from app.graph.nodes import TOOLS
from app.graph.prompts import SYSTEM_PROMPT

# Check tools are in TOOLS list
tool_names = [t.__name__ if hasattr(t, '__name__') else str(t) for t in TOOLS]
assert 'request_external_contact' in tool_names, "request_external_contact not in TOOLS"
assert 'nudge_external_contact' in tool_names, "nudge_external_contact not in TOOLS"

print("✓ All imports resolve")
print("✓ Tools registered in TOOLS list")
print("✓ Prompts load without error")
EOF
`

Expected: All assertions pass and output shows three checkmarks.

- [ ] **Step 2: Verify spec coverage**

Skim the spec and check:
- ✅ `requests` table created with correct schema
- ✅ `request_external_contact` tool implemented with all parameters
- ✅ `nudge_external_contact` tool implemented with lookup and nudge logic
- ✅ Email helpers in `email_sender.py`
- ✅ Tools registered in `TOOLS` list
- ✅ Prompt instructions added (both blocks)
- ✅ Tests written and passing

- [ ] **Step 3: Final commit summary**

Run: `git log --oneline -10`

Expected: 9-10 commits showing all tasks completed.

---

## Summary

All tasks complete once:
1. Migration created and committed
2. Tests written and all passing
3. Both tools fully implemented
4. Email helpers in place
5. Tools registered and wired
6. Prompt instructions added (both locations)
7. Full test suite passing with no regressions

Next: Execute this plan using subagent-driven-development or executing-plans.
