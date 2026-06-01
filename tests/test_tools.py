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
        "guardian_relationship": None,
        "messages": [],
        "modality_restriction": None,
    }
    base.update(kwargs)
    return base


def _make_supabase_client():
    execute = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
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


async def test_get_available_slots_bruna_age_exception_bypasses_under_12():
    """age_exception=True deve permitir paciente menor de 12 anos com Dra. Bruna."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]):
        result = await get_available_slots.coroutine(
            preferred_day="quarta",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", patient_age=8, age_exception=True),
            config=CONFIG,
        )
    assert "12 anos" not in result


async def test_get_available_slots_julio_rejects_patient_over_65():
    """Dr. Júlio não deve atender pacientes acima de 65 anos."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="julio", patient_age=70),
            config=CONFIG,
        )
    assert "65 anos" in result
    mock_slots.assert_not_called()


async def test_get_available_slots_julio_age_exception_bypasses_over_65():
    """age_exception=True deve permitir paciente acima de 65 anos com Dr. Júlio."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]):
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="julio", patient_age=70, age_exception=True),
            config=CONFIG,
        )
    assert "65 anos" not in result


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
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
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
    # Patch schedule data so the new weekday/window validation passes for any date,
    # letting us isolate the presencial_sob_consulta modality check.
    _sched = {"julio": {4: [(8, 0, 18, 0, "presencial_sob_consulta")]}}  # Friday full-day
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_modality_for_slot", return_value="presencial_sob_consulta"), \
         patch("app.google_calendar.DOCTOR_SCHEDULES", _sched), \
         patch("app.google_calendar.SCHEDULE_EXCEPTIONS", {}), \
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
    _sched = {"julio": {4: [(8, 0, 18, 0, "presencial_sob_consulta")]}}  # Friday full-day
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_modality_for_slot", return_value="presencial_sob_consulta"), \
         patch("app.google_calendar.DOCTOR_SCHEDULES", _sched), \
         patch("app.google_calendar.SCHEDULE_EXCEPTIONS", {}), \
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


async def test_confirm_appointment_respects_online_modality_restriction():
    """Se modality_restriction="online" no state, confirm_appointment ignora o modality arg."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rest-online") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="online"),
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — should be overridden
        )
    assert "evt-rest-online" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "online"


async def test_confirm_appointment_respects_presencial_modality_restriction():
    """Se modality_restriction="presencial" no state, confirm_appointment usa presencial."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rest-pres") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),
            config=CONFIG,
            modality="online",  # LLM passed online — should be overridden
        )
    assert "evt-rest-pres" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "presencial"


async def test_confirm_appointment_no_restriction_uses_slot_logic():
    """Sem restrição cadastral, a lógica de slot é aplicada normalmente."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-no-rest") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction=None),
            config=CONFIG,
            modality="presencial",
        )
    assert "evt-no-rest" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "presencial"


# ── cancel_appointment ────────────────────────────────────────────────────────

async def test_cancel_appointment_cancels_and_notifies():
    from app.graph.tools import cancel_appointment
    client, table, execute = _make_supabase_client()
    # maybe_single returns appointment data
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00"})
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
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
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00", "users": {"id": "user-1", "patient_name": "Maria", "name": "Maria", "number": "5583999999999"}})
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


async def test_reschedule_appointment_respects_modality_restriction():
    """reschedule_appointment deve respeitar modality_restriction do state."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    appt_data = {
        "start_time": "2026-03-20T09:00:00-03:00",
        "users": {"id": "user-1", "patient_name": "Maria", "name": "Maria", "number": "5583999999999"},
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_calendar.SCHEDULE_EXCEPTIONS", {}), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-orig",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="online"),
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — should be overridden
        )
    assert mock_update.called
    _, kwargs = mock_update.call_args
    assert kwargs.get("modality") == "online"


async def test_confirm_appointment_presencial_restriction_on_online_only_slot():
    """Restrição presencial NÃO pode sobrepor slot online-only — deve continuar online."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-onlineonly") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="online"):  # slot is online-only
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),  # restriction says presencial
            config=CONFIG,
            modality="presencial",
        )
    assert "evt-onlineonly" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "online"  # online-only wins over presencial restriction


# ── request_document ──────────────────────────────────────────────────────────

async def test_request_document_inserts_record_and_returns_success():
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
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
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
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


# ── register_payment ──────────────────────────────────────────────────────────

def _make_supabase_client_with_appointment():
    """Supabase client that serves register_payment's two sequential appointment queries.

    Call order:
      1. appts_result — appointments joined with users (patient resolution)
      2. appt_result  — full appointment details (payment logic)
      3+. update/upsert/linked-appts → generic empty response
    """
    # Call 1: new appointment-centric query with users join
    appts_with_users = MagicMock(data=[{
        "appointment_id": "apt-1",
        "start_time": "2026-03-23T09:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "status": "scheduled",
        "users": {"id": "user-123", "patient_name": "Maria", "name": "Maria"},
    }])
    # Call 2: full appointment fetch for payment logic
    apt_data = MagicMock(data=[{
        "appointment_id": "apt-1",
        "start_time": "2026-03-23T09:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "end_time": "2026-03-23T10:00:00+00:00",
        "paid_at": None,
        "booking_fee_paid_at": None,
        "status": "scheduled",
        "consultation_type": "retorno",
    }])
    empty = MagicMock(data=[])

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        if _side_effect.call_count == 1:
            return appts_with_users
        if _side_effect.call_count == 2:
            return apt_data
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
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
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


async def test_register_payment_sets_booking_fee_paid_at():
    """R$100 payment should set booking_fee_paid_at (taxa de reserva), not paid_at."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    # R$100 → taxa de reserva: only booking_fee_paid_at should be set, not paid_at
    update_calls = [c for c in table.update.call_args_list if "booking_fee_paid_at" in c[0][0]]
    assert len(update_calls) == 1
    paid_at_calls = [c for c in table.update.call_args_list if "paid_at" in c[0][0] and "booking_fee_paid_at" not in c[0][0]]
    assert len(paid_at_calls) == 0
    assert "taxa de reserva registrada" in result


async def test_register_payment_full_amount_sets_paid_at():
    """Full payment (>= expected) should set both paid_at and booking_fee_paid_at."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    # Full payment: both paid_at and booking_fee_paid_at should be set
    update_calls = [c for c in table.update.call_args_list if "paid_at" in c[0][0]]
    assert len(update_calls) == 1
    assert "QUITADA" in result


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


# ── request_registration_update ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_registration_update_email():
    """For field=email: updates DB AND sends notification email."""
    from app.graph.tools import request_registration_update

    state = _make_state(patient_name="Ana Souza")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="email",
            new_value="ana.novo@email.com",
            state=state,
            config=CONFIG,
        )

    # DB must be updated for email
    mock_upsert.assert_awaited_once()
    assert "ana.novo@email.com" in str(mock_upsert.call_args)

    # Notification must be sent
    mock_notify.assert_awaited_once()
    notify_call_str = str(mock_notify.call_args)
    assert "alteração cadastral" in notify_call_str.lower() or "Ana Souza" in notify_call_str

    assert "email" in result.lower()


@pytest.mark.asyncio
async def test_request_registration_update_other_field():
    """For non-email field: sends notification but does NOT update DB."""
    from app.graph.tools import request_registration_update

    state = _make_state(patient_name="Carlos Lima")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="CPF",
            new_value="123.456.789-00",
            state=state,
            config=CONFIG,
        )

    # DB must NOT be updated for non-email fields
    mock_upsert.assert_not_awaited()

    # Notification must still be sent
    mock_notify.assert_awaited_once()

    assert "CPF" in result or "cpf" in result.lower()


@pytest.mark.asyncio
async def test_request_registration_update_returns_confirmation():
    """Return value must mention the requested field."""
    from app.graph.tools import request_registration_update

    state = _make_state(patient_name="Beatriz")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="data de nascimento",
            new_value="15/03/1990",
            state=state,
            config=CONFIG,
        )

    assert "data de nascimento" in result.lower() or "data" in result.lower()
    # Bot stays active — no transfer indicator in return value
    assert "transfer" not in result.lower()
    assert "atendente" not in result.lower() or "equipe" in result.lower()


async def test_reschedule_appointment_presencial_restriction_on_online_only_slot():
    """Restrição presencial NÃO pode sobrepor slot online-only no reagendamento — deve continuar online."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    appt_data = {
        "start_time": "2026-03-20T09:00:00-03:00",
        "users": {"id": "user-1", "patient_name": "Maria", "name": "Maria", "number": "5583999999999"},
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_calendar.SCHEDULE_EXCEPTIONS", {}), \
         patch("app.google_calendar.get_modality_for_slot", return_value="online"):  # slot is online-only
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-orig",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),  # restriction says presencial
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — online-only slot should win
        )
    assert mock_update.called
    _, kwargs = mock_update.call_args
    assert kwargs.get("modality") == "online"  # online-only slot wins over presencial restriction
