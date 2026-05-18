"""Tests for each tool in app/graph/tools.py."""
import os
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from tests.conftest import PHONE, CONFIG

TZ = ZoneInfo("America/Recife")


def _make_state(**kwargs) -> dict:
    base = {
        "phone": PHONE,
        "stage": "patient_agent",
        "user_name": "Maria",
        "patient_name": "Maria",
        "patient_age": 30,
        "is_patient": True,
        "preferred_doctor": "julio",
        "is_for_self": True,
        "guardian_relationship": None,
        "messages": [],
    }
    base.update(kwargs)
    return base


def _make_supabase_client():
    execute = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("select", "eq", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


# ── get_available_slots ───────────────────────────────────────────────────────

async def test_get_available_slots_returns_formatted_list():
    from app.graph.tools import get_available_slots
    slots = [
        (datetime(2026, 3, 23, 9, 0, tzinfo=TZ), "escolha"),
        (datetime(2026, 3, 23, 10, 0, tzinfo=TZ), "online"),
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=slots):
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "09:00" in result
    assert "10:00" in result


async def test_get_available_slots_no_slots_returns_message():
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]):
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "Não encontrei horários disponíveis para segunda-feira" in result


async def test_get_available_slots_bruna_always_60min():
    """Dra. Bruna overrides slot_duration_minutes to 60 regardless of input."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]) as mock_slots:
        await get_available_slots.coroutine(
            preferred_day="quarta",
            preferred_shift="manha",
            slot_duration_minutes=120,
            state=_make_state(preferred_doctor="bruna"),
            config=CONFIG,
        )
    _, kwargs = mock_slots.call_args
    assert kwargs.get("slot_minutes") == 60 or mock_slots.call_args[0][3] == 60


async def test_get_available_slots_bruna_rejects_patient_under_12():
    """Dra. Bruna must not attend patients younger than 12."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="quarta",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", patient_age=8),
            config=CONFIG,
        )
    assert "12 anos" in result
    mock_slots.assert_not_called()


# ── confirm_appointment ───────────────────────────────────────────────────────

async def test_confirm_appointment_creates_event_and_notifies():
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-abc123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "evt-abc123" in result
    assert "Dr. Júlio" in result
    mock_notify.assert_called()  # clinic notified


async def test_confirm_appointment_with_session_note():
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-xyz"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
            session_note="1ª hora — responsáveis",
        )
    assert "1ª hora — responsáveis" in result


async def test_confirm_appointment_rolls_back_calendar_on_db_failure():
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    execute.side_effect = Exception("DB error")
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rollback"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "erro" in result.lower()
    mock_cancel.assert_awaited_once_with("cal123", "evt-rollback")


async def test_confirm_appointment_presencial_sob_consulta_blocked_without_silent_mode():
    """confirm_appointment deve bloquear presencial em slot presencial_sob_consulta fora do silent mode."""
    from app.graph.tools import confirm_appointment
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_modality_for_slot", return_value="presencial_sob_consulta"), \
         patch("app.google_calendar._credentials", side_effect=Exception("skip")):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-05-22T14:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
            modality="presencial",
        )
    assert "transfer_to_human" in result or "AÇÃO NECESSÁRIA" in result


async def test_confirm_appointment_presencial_sob_consulta_allowed_in_silent_mode():
    """confirm_appointment deve permitir presencial em slot presencial_sob_consulta quando silent_mode=True."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_modality_for_slot", return_value="presencial_sob_consulta"), \
         patch("app.google_calendar._credentials", side_effect=Exception("skip")), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-silent"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "u1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-05-22T14:00:00",
            slot_duration_minutes=60,
            state=_make_state(silent_mode=True),
            config=CONFIG,
            modality="presencial",
        )
    assert "evt-silent" in result or "confirmad" in result.lower()


# ── cancel_appointment ────────────────────────────────────────────────────────

async def test_cancel_appointment_cancels_and_notifies():
    from app.graph.tools import cancel_appointment
    client, table, execute = _make_supabase_client()
    # maybe_single returns appointment data
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00"})
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await cancel_appointment.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "cancelada" in result.lower()
    mock_cancel.assert_awaited_once_with("cal123", "evt-abc")
    mock_notify.assert_called()


# ── reschedule_appointment ────────────────────────────────────────────────────

async def test_reschedule_appointment_updates_event_and_notifies():
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00"})
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "remarcada" in result.lower()
    mock_update.assert_awaited_once()
    mock_notify.assert_called()


# ── request_document ──────────────────────────────────────────────────────────

async def test_request_document_inserts_record_and_returns_success():
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_document_request", new_callable=AsyncMock), \
         patch("app.email_sender.send_document_request_email", new_callable=AsyncMock):
        result = await request_document.coroutine(
            document_type="nota_fiscal",
            patient_email="maria@example.com",
            state=_make_state(),
            config=CONFIG,
        )
    assert "nota_fiscal" in result
    assert "✅" in result


async def test_request_document_succeeds_even_if_sheets_and_email_fail():
    """Fire-and-forget: sheets/email errors must not surface."""
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_document_request", side_effect=Exception("sheets down")), \
         patch("app.email_sender.send_document_request_email", side_effect=Exception("smtp down")):
        result = await request_document.coroutine(
            document_type="laudo",
            patient_email="maria@example.com",
            state=_make_state(),
            config=CONFIG,
        )
    assert "✅" in result


# ── transfer_to_human ─────────────────────────────────────────────────────────

async def test_transfer_to_human_deactivates_user():
    from app.graph.tools import transfer_to_human
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock):
        result = await transfer_to_human.coroutine(
            reason="Paciente quer falar com humano",
            state=_make_state(),
            config=CONFIG,
        )
    mock_upsert.assert_awaited_once()
    call_kwargs = mock_upsert.call_args[0]
    assert call_kwargs[1]["active"] is False
    assert "deactivated_at" in call_kwargs[1]
    assert "atendente" in result.lower()


async def test_transfer_to_human_adds_private_note_to_chatwoot():
    """On human transfer, a private note with patient context is added to Chatwoot."""
    from app.graph.tools import transfer_to_human
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation(PHONE, 42)

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.graph.tools.unassign_agent_bot", new_callable=AsyncMock), \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await transfer_to_human.coroutine(
            reason="Paciente escolheu presencial",
            state=_make_state(),
            config=CONFIG,
        )
    mock_note.assert_awaited_once()
    note_text = mock_note.call_args[0][1]
    assert "Transferido pelo bot" in note_text
    assert "Paciente escolheu presencial" in note_text


async def test_transfer_to_human_sends_only_to_user():
    """transfer_to_human returns the message directly (no send_text call); message goes to patient via LangGraph."""
    from app.graph.tools import transfer_to_human
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock):
        result = await transfer_to_human.coroutine(
            reason="teste",
            state=_make_state(),
            config=CONFIG,
        )
    assert mock_send.await_count == 0
    assert "transferir" in result.lower() or "encaminhar" in result.lower()


# ── confirm_attendance ────────────────────────────────────────────────────────

async def test_confirm_attendance_sets_confirmed_at():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await confirm_attendance.coroutine(
            appointment_id="evt-abc123",
            state=_make_state(),
            config=CONFIG,
        )
    assert "confirmada" in result.lower()
    # Verify the update was called with confirmed_at
    update_call = table.update.call_args[0][0]
    assert "confirmed_at" in update_call


# ── register_payment ──────────────────────────────────────────────────────────

def _make_supabase_client_with_appointment():
    """Supabase client whose execute returns a scheduled appointment by default."""
    apt_data = MagicMock(data=[{"appointment_id": "apt-1", "start_time": "2026-03-23T09:00:00+00:00"}])
    execute = AsyncMock(return_value=apt_data)
    table = MagicMock()
    for m in ("select", "eq", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


async def test_register_payment_appends_sheet_and_notifies():
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )

    assert "✅" in result
    mock_sheets.assert_awaited_once()
    sheets_kwargs = mock_sheets.call_args
    assert "Maria" in sheets_kwargs[0][0]          # patient_name
    assert "100,00" in sheets_kwargs[0][4]         # amount
    assert "https://drive.google.com" in sheets_kwargs[0][5]  # drive_link
    mock_notify.assert_called()
    notify_msg = mock_notify.call_args[0][0]       # message is first positional arg
    assert "Maria" in notify_msg
    assert "https://drive.google.com" in notify_msg


async def test_register_payment_rename_failure_still_succeeds():
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock, side_effect=Exception("Drive unavailable")), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert "✅" in result


async def test_transfer_to_human_unassigns_chatwoot_bot(mock_send_text):
    """When bot hands off to human, Chatwoot agent bot is unassigned for that conversation."""
    from app.graph.tools import transfer_to_human
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 77)

    config = {
        "configurable": {
            "phone": "5511999999999@s.whatsapp.net",
            "thread_id": "5511999999999@s.whatsapp.net",
        }
    }
    state = {"user_name": "João", "patient_name": "João"}

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock), \
         patch("app.graph.tools.unassign_agent_bot", new_callable=AsyncMock) as mock_unassign:
        await transfer_to_human.ainvoke(
            {"reason": "paciente quer falar com atendente", "state": state},
            config=config,
        )
        mock_unassign.assert_called_once_with(77)


async def test_register_payment_sets_paid_at():
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    # Verify paid_at was set in an update call
    update_calls = [c for c in table.update.call_args_list if "paid_at" in c[0][0]]
    assert len(update_calls) == 1


# ── update_patient_ages script logic ─────────────────────────────────────────

def test_age_from_birth_date_dd_mm_yyyy():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    # Birthday already passed this year
    assert _age_from_birth_date("10/03/1990", today) == 36
    # Birthday not yet reached this year
    assert _age_from_birth_date("20/07/1990", today) == 35


def test_age_from_birth_date_iso():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    assert _age_from_birth_date("1990-03-10", today) == 36


def test_age_from_birth_date_exact_birthday():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    assert _age_from_birth_date("18/05/1990", today) == 36  # birthday today → counts


def test_age_from_birth_date_minor():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    assert _age_from_birth_date("15/03/2015", today) == 11
    assert _age_from_birth_date("15/03/2015", today) < 18


def test_age_from_birth_date_invalid_returns_none():
    from scripts.update_patient_ages import _age_from_birth_date
    assert _age_from_birth_date("not-a-date") is None
    assert _age_from_birth_date("") is None
    assert _age_from_birth_date(None) is None


async def test_update_patient_ages_only_updates_changed():
    """Script must update only rows where age differs from birth_date calculation."""
    from scripts.update_patient_ages import main
    from datetime import date
    from unittest.mock import AsyncMock, MagicMock, patch

    today = date(2026, 5, 18)

    users = [
        # age already correct — must NOT be updated
        {"id": "u1", "name": "Alice", "patient_name": None, "birth_date": "10/03/1990", "age": 36},
        # age wrong (didn't update last year) — must be updated
        {"id": "u2", "name": "Bob",   "patient_name": None, "birth_date": "10/03/1990", "age": 35},
        # no stored age — must be updated
        {"id": "u3", "name": "Carol", "patient_name": None, "birth_date": "20/07/2015", "age": None},
    ]

    # Build a single chain mock that handles all builder patterns:
    # .select().not_.is_().execute()  AND  .update().eq().execute()
    chain = MagicMock()
    chain.execute = AsyncMock(return_value=MagicMock(data=users))
    chain.not_ = chain        # attribute access (not a call)
    chain.is_.return_value = chain
    chain.eq.return_value = chain

    table = MagicMock()
    table.select.return_value = chain
    # Each .update() call must return a fresh chain with its own execute tracker
    update_execute = AsyncMock(return_value=MagicMock(data=[]))
    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.execute = update_execute
    table.update.return_value = update_chain

    client = MagicMock()
    client.from_.return_value = table

    with patch("scripts.update_patient_ages.date") as mock_date, \
         patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client):
        mock_date.today.return_value = today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await main()

    # update() should have been called exactly twice (u2 and u3)
    assert table.update.call_count == 2
    updated_ids = {call.args[0]["age"] for call in table.update.call_args_list}
    # Both updates set age=36 (u2: corrects stale age; u3: was None → now 10)
    # u3 born 20/07/2015, today 18/05/2026 → age 10
    ages_written = [call.args[0]["age"] for call in table.update.call_args_list]
    assert 36 in ages_written   # u2 corrected
    assert 10 in ages_written   # u3 filled in
