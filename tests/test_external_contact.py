"""Tests for external contact request tools in app/graph/tools.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from tests.conftest import PHONE, CONFIG


def _make_state(**kwargs) -> dict:
    """Build a test state dict with defaults."""
    base = {
        "phone": PHONE,
        "stage": "patient_agent",
        "patient_name": "Suzi Monteiro Viana",
        "patient_age": 17,
        "user_db_id": "user-123",
        "preferred_doctor": "julio",
        "patient_email": "suzi@example.com",
        "messages": [],
    }
    base.update(kwargs)
    return base


def _make_mock_supabase_with_tables(**table_configs) -> tuple:
    """Build a mock Supabase client with configurable table responses.

    Args:
        **table_configs: Dict of {table_name: data_or_dict}
            doctors: data to return from doctors.single().execute()
            requests: data to return from requests.insert().execute()

    Returns:
        (mock_client, tables_dict) where tables_dict has table names as keys
    """
    mock_client = MagicMock()
    tables = {}

    # Build doctors table mock
    if "doctors" in table_configs:
        doctors_data = table_configs["doctors"]
        mock_doctors_table = MagicMock()
        mock_doctors_table.select.return_value = mock_doctors_table
        mock_doctors_table.eq.return_value = mock_doctors_table
        mock_doctors_table.single.return_value = mock_doctors_table
        execute_doctors = AsyncMock(return_value=MagicMock(data=doctors_data))
        mock_doctors_table.execute = execute_doctors
        tables["doctors"] = mock_doctors_table

    # Build requests table mock
    if "requests" in table_configs:
        mock_requests_table = MagicMock()
        mock_requests_table.insert.return_value = mock_requests_table
        execute_requests = AsyncMock(return_value=MagicMock(data=[]))
        mock_requests_table.execute = execute_requests
        tables["requests"] = mock_requests_table

    # Setup from_() to route to correct table
    def from_side_effect(table_name):
        return tables.get(table_name, MagicMock())

    mock_client.from_ = MagicMock(side_effect=from_side_effect)

    return mock_client, tables


# ── request_external_contact ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_external_contact_success():
    """Test successful external contact request registration and email sending."""
    from app.graph.tools import request_external_contact

    state = _make_state()
    config = {"configurable": {"phone": PHONE}}

    mock_client, tables = _make_mock_supabase_with_tables(
        doctors={"agenda_id": "dr.juliogouveia@gmail.com"},
        requests={},
    )

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=mock_client), \
         patch("app.email_sender.send_external_contact_request_email", new_callable=AsyncMock) as mock_email, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:

        result = await request_external_contact.coroutine(
            third_party_role="psicóloga",
            third_party_name="Bruna Psicóloga",
            reason="acompanhamento antes da consulta de 22/07",
            state=state,
            config=config,
            third_party_contact="bruna.psico@example.com",
        )

    # Assertions
    assert "registrado" in result.lower() or "encaminhamos" in result.lower()

    # Verify insert was called with correct data
    tables["requests"].insert.assert_called_once()
    call_args = tables["requests"].insert.call_args
    inserted_data = call_args[0][0]

    # Type check
    assert isinstance(inserted_data, dict), "Expected dict payload for insert"

    assert inserted_data["type"] == "contato_terceiro"
    assert inserted_data["phone"] == PHONE
    assert inserted_data["patient_name"] == "Suzi Monteiro Viana"
    assert "psicóloga" in inserted_data["content"].lower()

    # Verify metadata has type-specific fields
    metadata = inserted_data["metadata"]
    assert isinstance(metadata, dict), "Expected metadata to be dict"
    assert metadata["third_party_role"] == "psicóloga"
    assert metadata["third_party_name"] == "Bruna Psicóloga"
    assert metadata["third_party_contact"] == "bruna.psico@example.com"

    # Verify email was sent with correct details
    mock_email.assert_called_once()
    email_kwargs = mock_email.call_args.kwargs
    assert email_kwargs["doctor_email"] == "dr.juliogouveia@gmail.com"
    assert email_kwargs["patient_name"] == "Suzi Monteiro Viana"
    assert email_kwargs["third_party_name"] == "Bruna Psicóloga"
    assert email_kwargs["third_party_role"] == "psicóloga"

    # Verify event was logged with correct event type
    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == "external_contact_requested"
    log_data = mock_log.call_args[0][2]
    assert isinstance(log_data, dict), "Expected log_event to receive dict"
    assert log_data["third_party_role"] == "psicóloga"

    # Verify clinic was notified
    mock_notify.assert_called_once()
    notify_msg = mock_notify.call_args[0][0]
    assert "Bruna Psicóloga" in notify_msg


@pytest.mark.asyncio
async def test_request_external_contact_missing_patient_name():
    """Test fallback to get_user_by_phone when patient_name not in state."""
    from app.graph.tools import request_external_contact

    state = _make_state(patient_name=None)
    config = {"configurable": {"phone": PHONE}}

    mock_user = {"patient_name": "Fallback Patient Name"}

    mock_client, tables = _make_mock_supabase_with_tables(
        doctors={"agenda_id": "dr.juliogouveia@gmail.com"},
        requests={},
    )

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=mock_client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=mock_user) as mock_get_user, \
         patch("app.email_sender.send_external_contact_request_email", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):

        result = await request_external_contact.coroutine(
            third_party_role="terapeuta",
            third_party_name="João Terapeuta",
            reason="acompanhamento",
            state=state,
            config=config,
        )

    # Verify fallback name was used in the insert
    call_args = tables["requests"].insert.call_args
    inserted_data = call_args[0][0]
    assert inserted_data["patient_name"] == "Fallback Patient Name"
    assert isinstance(inserted_data, dict), "Expected dict payload"

    # Verify fallback name appears in metadata too
    metadata = inserted_data["metadata"]
    assert metadata["third_party_name"] == "João Terapeuta"

    # Verify get_user_by_phone was called as fallback
    mock_get_user.assert_called_once_with(PHONE)


@pytest.mark.asyncio
async def test_request_external_contact_without_third_party_contact():
    """Test that third_party_contact is optional and handled gracefully."""
    from app.graph.tools import request_external_contact

    state = _make_state()
    config = {"configurable": {"phone": PHONE}}

    mock_client, tables = _make_mock_supabase_with_tables(
        doctors={"agenda_id": "dr.juliogouveia@gmail.com"},
        requests={},
    )

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=mock_client), \
         patch("app.email_sender.send_external_contact_request_email", new_callable=AsyncMock) as mock_email, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):

        result = await request_external_contact.coroutine(
            third_party_role="psicóloga",
            third_party_name="Bruna Psicóloga",
            reason="acompanhamento",
            state=state,
            config=config,
            # third_party_contact not provided
        )

    # Verify it still works without third_party_contact
    assert "registrado" in result.lower() or "encaminhamos" in result.lower()

    # Verify metadata handles missing contact gracefully
    call_args = tables["requests"].insert.call_args
    inserted_data = call_args[0][0]
    metadata = inserted_data["metadata"]
    assert "third_party_contact" in metadata
    # Should be None or empty string
    assert metadata["third_party_contact"] in ("", None)

    # Verify email was still sent
    mock_email.assert_called_once()


@pytest.mark.asyncio
async def test_request_external_contact_handles_missing_doctor_email():
    """Test graceful handling when doctor email cannot be fetched."""
    from app.graph.tools import request_external_contact

    state = _make_state()
    config = {"configurable": {"phone": PHONE}}

    mock_client, tables = _make_mock_supabase_with_tables(
        doctors=None,  # Doctor lookup returns no data
        requests={},
    )

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=mock_client), \
         patch("app.email_sender.send_external_contact_request_email", new_callable=AsyncMock) as mock_email, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):

        result = await request_external_contact.coroutine(
            third_party_role="psicóloga",
            third_party_name="Bruna Psicóloga",
            reason="acompanhamento",
            state=state,
            config=config,
        )

    # Verify request was still registered despite missing email
    assert "registrado" in result.lower() or "encaminhamos" in result.lower()
    tables["requests"].insert.assert_called_once()

    # Verify email send was attempted (but caught exception in tool)
    mock_email.assert_called_once()
    # Tool should pass empty email string if lookup fails
    email_kwargs = mock_email.call_args.kwargs
    assert isinstance(email_kwargs["doctor_email"], (str, type(None)))


# ── nudge_external_contact ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nudge_external_contact_success():
    """Test successful nudge when a pending external contact request exists."""
    from app.graph.tools import nudge_external_contact

    state = _make_state()
    config = {"configurable": {"phone": PHONE}}

    # Mock doctor data
    doctor_data = {"agenda_id": "dr.juliogouveia@gmail.com"}

    # Mock existing request row with pending request
    existing_request = {
        "id": "req-123",
        "created_at": "2026-07-20T10:30:00Z",
        "type": "contato_terceiro",
        "phone": PHONE,
        "patient_name": "Suzi Monteiro Viana",
        "doctor_id": "julio",
        "metadata": {
            "third_party_name": "Bruna Psicóloga",
            "third_party_role": "psicóloga",
            "third_party_contact": "bruna.psico@example.com",
        },
    }

    # Build mock Supabase client with select chain for requests table
    mock_client = MagicMock()

    # Mock doctors table (single row return)
    mock_doctors_table = MagicMock()
    mock_doctors_table.select.return_value = mock_doctors_table
    mock_doctors_table.eq.return_value = mock_doctors_table
    mock_doctors_table.single.return_value = mock_doctors_table
    execute_doctors = AsyncMock(return_value=MagicMock(data=doctor_data))
    mock_doctors_table.execute = execute_doctors

    # Mock requests table (select chain with filters)
    mock_requests_table = MagicMock()
    mock_requests_table.select.return_value = mock_requests_table
    mock_requests_table.eq.return_value = mock_requests_table
    mock_requests_table.order.return_value = mock_requests_table
    mock_requests_table.limit.return_value = mock_requests_table
    execute_requests = AsyncMock(return_value=MagicMock(data=[existing_request]))
    mock_requests_table.execute = execute_requests

    def from_side_effect(table_name):
        if table_name == "doctors":
            return mock_doctors_table
        elif table_name == "requests":
            return mock_requests_table
        return MagicMock()

    mock_client.from_ = MagicMock(side_effect=from_side_effect)

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=mock_client), \
         patch("app.email_sender.send_external_contact_nudge_email", new_callable=AsyncMock) as mock_email, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:

        result = await nudge_external_contact.coroutine(
            patient_message="O Dr. Júlio ainda não falou com a psicóloga?",
            state=state,
            config=config,
        )

    # Assertions
    assert "aviso" in result.lower() or "encaminhamos" in result.lower()

    # Verify requests table was queried with correct filters
    mock_requests_table.select.assert_called()
    # Check that eq was called for both phone and type filters
    eq_calls = mock_requests_table.eq.call_args_list
    assert len(eq_calls) >= 2, "Expected at least 2 eq() calls for phone and type filters"

    # Verify order and limit were called for getting latest request
    mock_requests_table.order.assert_called()
    mock_requests_table.limit.assert_called()

    # Verify email was sent with correct parameters
    mock_email.assert_called_once()
    email_kwargs = mock_email.call_args.kwargs
    assert email_kwargs["doctor_email"] == "dr.juliogouveia@gmail.com"
    assert email_kwargs["patient_name"] == "Suzi Monteiro Viana"
    assert email_kwargs["third_party_name"] == "Bruna Psicóloga"
    assert email_kwargs["third_party_role"] == "psicóloga"
    assert email_kwargs["patient_message"] == "O Dr. Júlio ainda não falou com a psicóloga?"
    assert email_kwargs["created_at"] == "2026-07-20T10:30:00Z"

    # Verify event was logged
    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == "external_contact_nudge_sent"
    log_data = mock_log.call_args[0][2]
    assert isinstance(log_data, dict)
    assert log_data.get("third_party_name") == "Bruna Psicóloga"
    assert log_data.get("third_party_role") == "psicóloga"


@pytest.mark.asyncio
async def test_nudge_external_contact_no_pending_request():
    """Test nudge when no pending external contact request exists."""
    from app.graph.tools import nudge_external_contact

    state = _make_state()
    config = {"configurable": {"phone": PHONE}}

    # Build mock Supabase client with empty requests
    mock_client = MagicMock()

    # Mock doctors table (won't be called)
    mock_doctors_table = MagicMock()
    mock_doctors_table.select.return_value = mock_doctors_table
    mock_doctors_table.eq.return_value = mock_doctors_table
    mock_doctors_table.single.return_value = mock_doctors_table
    execute_doctors = AsyncMock(return_value=MagicMock(data=None))
    mock_doctors_table.execute = execute_doctors

    # Mock requests table (return empty list - no pending request)
    mock_requests_table = MagicMock()
    mock_requests_table.select.return_value = mock_requests_table
    mock_requests_table.eq.return_value = mock_requests_table
    mock_requests_table.order.return_value = mock_requests_table
    mock_requests_table.limit.return_value = mock_requests_table
    execute_requests = AsyncMock(return_value=MagicMock(data=[]))
    mock_requests_table.execute = execute_requests

    def from_side_effect(table_name):
        if table_name == "doctors":
            return mock_doctors_table
        elif table_name == "requests":
            return mock_requests_table
        return MagicMock()

    mock_client.from_ = MagicMock(side_effect=from_side_effect)

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=mock_client), \
         patch("app.email_sender.send_external_contact_nudge_email", new_callable=AsyncMock) as mock_email, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:

        result = await nudge_external_contact.coroutine(
            patient_message="E o psicólogo que foi pedido?",
            state=state,
            config=config,
        )

    # Assertions
    assert "não encontramos" in result.lower() or "nenhum pedido" in result.lower()

    # Verify requests table was queried
    mock_requests_table.select.assert_called()

    # Verify email was NOT sent (no pending request to nudge)
    mock_email.assert_not_called()

    # Verify event was NOT logged (no action taken)
    mock_log.assert_not_called()
