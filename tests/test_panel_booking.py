import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import panel_booking as pb
from app.database import DOCTOR_IDS

TZ = ZoneInfo("America/Recife")
MON_9 = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


def _sb(rows=None):
    """Cliente Supabase encadeável que devolve `rows` em qualquer select."""
    client = MagicMock()
    q = MagicMock()
    for m in ("select", "eq", "neq", "in_", "lt", "gt", "gte", "order", "limit", "insert", "update"):
        getattr(q, m).return_value = q
    q.execute = AsyncMock(return_value=MagicMock(data=rows or []))
    client.from_.return_value = q
    return client, q


# ── check_slot ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_slot_free():
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        assert await pb.check_slot("julio", MON_9, 60, patient_id="p1") == []


@pytest.mark.asyncio
async def test_check_slot_outside_grid_reason():
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", datetime(2026, 10, 5, 13, 0, tzinfo=TZ), 60, patient_id="p1")
    assert reasons == ["fora do horário de atendimento"]


@pytest.mark.asyncio
async def test_check_slot_clash_names_other_patient():
    clash = [{"patient_id": "p9", "start_time": "2026-10-05T12:00:00+00:00", "patients": {"name": "Rafael Lima"}}]
    client, _ = _sb(clash)
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["bate com a consulta de Rafael às 09:00"]


@pytest.mark.asyncio
async def test_check_slot_calendar_busy():
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock,
               return_value=[{"start": "2026-10-05T09:00:00-03:00"}]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["agenda do médico ocupada às 09:00"]


@pytest.mark.asyncio
async def test_check_slot_same_patient_is_still_a_conflict():
    """Revisão: duas consultas do mesmo paciente no mesmo horário também são
    conflito — o painel não deve mais isentar o próprio patient_id."""
    clash = [{"patient_id": "p1", "start_time": "2026-10-05T12:00:00+00:00", "patients": {"name": "Lucas Menezes"}}]
    client, _ = _sb(clash)
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["bate com a consulta de Lucas às 09:00"]


@pytest.mark.asyncio
async def test_check_slot_dedup_db_and_calendar_same_time():
    clash = [{"patient_id": "p9", "start_time": "2026-10-05T12:00:00+00:00", "patients": {"name": "Rafael Lima"}}]
    client, _ = _sb(clash)
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock,
               return_value=[{"start": "2026-10-05T09:00:00-03:00"}]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["bate com a consulta de Rafael às 09:00"]


@pytest.mark.asyncio
async def test_check_slot_clash_all_caps_name_title_cased():
    clash = [{"patient_id": "p9", "start_time": "2026-10-05T12:00:00+00:00", "patients": {"name": "RAFAEL LIMA"}}]
    client, _ = _sb(clash)
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["bate com a consulta de Rafael às 09:00"]


@pytest.mark.asyncio
async def test_check_slot_query_filters_applied():
    client, q = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    end = MON_9 + timedelta(minutes=60)
    q.eq.assert_any_call("doctor_id", DOCTOR_IDS["julio"])
    q.eq.assert_any_call("status", "scheduled")
    q.lt.assert_called_once_with("start_time", end.isoformat())
    q.gt.assert_called_once_with("end_time", MON_9.isoformat())


@pytest.mark.asyncio
async def test_check_slot_accepts_utc_datetime_converts_to_recife():
    utc_start = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)  # == 09:00 Recife (UTC-3)
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", utc_start, 60, patient_id="p1")
    assert reasons == []


# ── create_appointments ──────────────────────────────────────────────────────

def _req(**kw):
    base = {
        "phone": "5581999998888@s.whatsapp.net", "contact_id": "c1",
        "patient": {"id": "p1", "name": "LUCAS MENEZES", "social_name": None, "email": "l@x.com",
                    "birth_date": "10/02/1990", "custom_price": None, "booking_fee_waived": False},
        "doctor": "julio", "modality": "presencial",
        "parts": [{"start": MON_9, "minutes": 60}], "split": False, "split_of": None,
        "session_note": "", "first_consultation": False, "billing": "normal",
        "encaixe": False, "agent": "Maria",
    }
    base.update(kw)
    return base


def _patches(client, event_ids=("evt1", "evt2")):
    return [
        patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client),
        patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"),
        patch("app.google_calendar.create_event", new_callable=AsyncMock, side_effect=list(event_ids)),
        patch("app.google_calendar.cancel_event", new_callable=AsyncMock),
        patch("app.panel_booking.log_event", new_callable=AsyncMock),
        patch("app.panel_booking._notify_clinic_async"),
    ]


async def _run(req, client, **kw):
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client, **kw)]
        out = await pb.create_appointments(req)
    return out, mocks


@pytest.mark.asyncio
async def test_create_single_normal():
    client, q = _sb([])
    out, mocks = await _run(_req(), client)
    rows = q.insert.call_args[0][0]
    row = rows[0]
    assert row["appointment_id"] == "evt1"
    assert row["contact_id"] == "c1"
    assert row["patient_id"] == "p1"
    assert row["status"] == "scheduled"
    assert row["booking_fee_waived"] is False and row["booking_fee_paid_at"] is None
    assert row["is_courtesy"] is False
    assert row["consultation_type"] is None
    assert out["kind"] == "normal"
    ev = mocks[4].call_args
    assert ev[0][0] == "appointment_booked"
    assert ev[0][2]["origem"] == "painel" and ev[0][2]["atendente"] == "Maria" and ev[0][2]["encaixe"] is False


@pytest.mark.asyncio
async def test_create_courtesy_marks_fee_waived_too():
    client, q = _sb([])
    out, _ = await _run(_req(billing="cortesia"), client)
    row = q.insert.call_args[0][0][0]
    assert row["is_courtesy"] is True
    assert row["booking_fee_waived"] is True and row["booking_fee_paid_at"]
    assert out["kind"] == "cortesia"


@pytest.mark.asyncio
async def test_create_fee_waived():
    client, q = _sb([])
    out, _ = await _run(_req(billing="taxa_isenta"), client)
    row = q.insert.call_args[0][0][0]
    assert row["booking_fee_waived"] is True and row["is_courtesy"] is False
    assert out["kind"] == "taxa_isenta"


@pytest.mark.asyncio
async def test_create_custom_price_zero_forces_courtesy_even_if_billing_normal():
    client, q = _sb([])
    patient = {"id": "p1", "name": "LUCAS MENEZES", "social_name": None, "email": "l@x.com",
               "birth_date": "10/02/1990", "custom_price": 0, "booking_fee_waived": False}
    out, _ = await _run(_req(patient=patient, billing="normal"), client)
    row = q.insert.call_args[0][0][0]
    assert row["is_courtesy"] is True
    assert row["booking_fee_waived"] is True
    assert out["kind"] == "cortesia"


@pytest.mark.asyncio
async def test_create_minor_julio_non_first_sets_acompanhamento():
    """Menor com Dr. Júlio, sem marcar 1ª consulta, grava consultation_type=acompanhamento
    (regra usada depois por register_payment/pricing em app/graph/tools.py)."""
    client, q = _sb([])
    minor = {"id": "p1", "name": "LUCAS MENEZES", "social_name": None, "email": "l@x.com",
             "birth_date": "10/02/2016", "custom_price": None, "booking_fee_waived": False}
    out, _ = await _run(_req(patient=minor), client)
    row = q.insert.call_args[0][0][0]
    assert row["consultation_type"] == "acompanhamento"


@pytest.mark.asyncio
async def test_create_split_both_parts():
    client, q = _sb([])
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    out, mocks = await _run(_req(split=True, first_consultation=True,
                                 parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}]), client)
    rows = q.insert.call_args[0][0]
    assert [r["session_note"] for r in rows] == ["1ª consulta · parte 1 de 2", "1ª consulta · parte 2 de 2"]
    assert all(r["consultation_type"] == "primeira_consulta" for r in rows)
    assert mocks[2].await_count == 2
    assert len(out["appointments"]) == 2


@pytest.mark.asyncio
async def test_create_split_forces_primeira_consulta_even_if_flag_false():
    client, q = _sb([])
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    out, _ = await _run(_req(split=True, first_consultation=False,
                             parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}]), client)
    rows = q.insert.call_args[0][0]
    assert all(r["consultation_type"] == "primeira_consulta" for r in rows)


@pytest.mark.asyncio
async def test_create_split_part1_only_pending_part2_true_label_part1():
    client, q = _sb([])
    out, _ = await _run(_req(split=True, first_consultation=True,
                             parts=[{"start": MON_9, "minutes": 60}]), client, event_ids=("evt1",))
    rows = q.insert.call_args[0][0]
    assert rows[0]["session_note"] == "1ª consulta · parte 1 de 2"
    assert out["pending_part2"] is True
    assert len(out["appointments"]) == 1


@pytest.mark.asyncio
async def test_complete_pending_part2_inherits_fee():
    client, q = _sb([])
    part1 = {"appointment_id": "evtP1", "booking_fee_paid_at": "2026-09-30T10:00:00-03:00",
             "booking_fee_waived": False, "is_courtesy": False}
    out, _ = await _run(_req(split_of=part1, first_consultation=True), client)
    row = q.insert.call_args[0][0][0]
    assert row["session_note"] == "1ª consulta · parte 2 de 2"
    assert row["booking_fee_paid_at"] == "2026-09-30T10:00:00-03:00"
    assert out["kind"] == "taxa_paga"


@pytest.mark.asyncio
async def test_complete_pending_part2_billing_cortesia_overrides_unpaid_part1():
    client, q = _sb([])
    part1 = {"appointment_id": "evtP1", "booking_fee_paid_at": None,
             "booking_fee_waived": False, "is_courtesy": False}
    out, _ = await _run(_req(split_of=part1, first_consultation=True, billing="cortesia"), client)
    row = q.insert.call_args[0][0][0]
    assert row["is_courtesy"] is True
    assert row["booking_fee_waived"] is True
    assert out["kind"] == "cortesia"


@pytest.mark.asyncio
async def test_complete_pending_part2_paid_part1_billing_normal_stays_taxa_paga():
    client, q = _sb([])
    part1 = {"appointment_id": "evtP1", "booking_fee_paid_at": "2026-09-30T10:00:00-03:00",
             "booking_fee_waived": False, "is_courtesy": False}
    out, _ = await _run(_req(split_of=part1, first_consultation=True, billing="normal"), client)
    row = q.insert.call_args[0][0][0]
    assert row["booking_fee_paid_at"] == "2026-09-30T10:00:00-03:00"
    assert row["booking_fee_waived"] is False
    assert out["kind"] == "taxa_paga"


@pytest.mark.asyncio
async def test_complete_pending_part2_courtesy_part1_stays_cortesia():
    client, q = _sb([])
    part1 = {"appointment_id": "evtP1", "booking_fee_paid_at": None,
             "booking_fee_waived": True, "is_courtesy": True}
    out, _ = await _run(_req(split_of=part1, first_consultation=True, billing="normal"), client)
    row = q.insert.call_args[0][0][0]
    assert row["is_courtesy"] is True
    assert out["kind"] == "cortesia"


@pytest.mark.asyncio
async def test_create_appointments_normalizes_utc_start_to_recife():
    client, q = _sb([])
    utc_start = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    out, _ = await _run(_req(parts=[{"start": utc_start, "minutes": 60}]), client)
    row = q.insert.call_args[0][0][0]
    assert row["start_time"] == datetime(2026, 10, 5, 9, 0, tzinfo=TZ).isoformat()


@pytest.mark.asyncio
async def test_create_appointments_mismatched_parts_and_notes_raises():
    client, q = _sb([])
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    thu = datetime(2026, 10, 10, 9, 0, tzinfo=TZ)
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client)]
        with pytest.raises(pb.BookingError):
            await pb.create_appointments(_req(
                split=True, first_consultation=True,
                parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}, {"start": thu, "minutes": 60}],
            ))
    mocks[2].assert_not_called()  # create_event: nunca chega a criar nada


@pytest.mark.asyncio
async def test_create_calendar_id_lookup_failure_raises_booking_error():
    client, q = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock,
               side_effect=RuntimeError("boom")), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.panel_booking.log_event", new_callable=AsyncMock):
        with pytest.raises(pb.BookingError):
            await pb.create_appointments(_req())
    mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_create_notifies_clinic_with_modality_label():
    client, q = _sb([])
    with ExitStack() as st:
        mocks = []
        for p in _patches(client):
            mocks.append(st.enter_context(p))
        notify_mock = mocks[5]
        await pb.create_appointments(_req(modality="online"))
    body = notify_mock.call_args[0][1]
    assert "Modalidade: Online" in body


@pytest.mark.asyncio
async def test_rollback_deletes_all_events_on_db_failure():
    client, q = _sb([])
    q.execute = AsyncMock(side_effect=RuntimeError("db down"))
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client)]
        with pytest.raises(pb.BookingError):
            await pb.create_appointments(_req(
                split=True, first_consultation=True,
                parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}],
            ))
    cancel_mock = mocks[3]
    assert cancel_mock.await_count == 2
    calls = [c.args for c in cancel_mock.call_args_list]
    assert ("cal", "evt1") in calls and ("cal", "evt2") in calls
    mocks[4].assert_not_awaited()  # log_event
    mocks[5].assert_not_called()  # _notify_clinic_async


@pytest.mark.asyncio
async def test_rollback_calls_cancel_event():
    client, q = _sb([])
    q.execute = AsyncMock(side_effect=RuntimeError("db down"))
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client)]
        with pytest.raises(pb.BookingError):
            await pb.create_appointments(_req())
    mocks[3].assert_awaited_once_with("cal", "evt1")


# ── mensagem ao paciente ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_recipients_follow_age_rule_and_report_held():
    contacts = [{"id": "c1", "phone": "5581999998888", "name": "Carla Menezes", "manual_hold": False}]
    linked = [{"contact": {"id": "c2", "name": "Paulo", "phone": "5581911112222", "manual_hold": True},
               "is_self": False, "relationship": "pai"}]
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=contacts) as m, \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=linked):
        out = await pb.message_preview("p1", "c1", "normal", ["Dr. Júlio — x"], pending_part2=False)
    m.assert_awaited_once_with("p1", {"contact_id": "c1"})
    assert out["recipients"] == [{"name": "Carla Menezes", "phone_hint": "8888"}]
    assert out["held"] == ["Paulo"]
    assert out["text"].startswith("Consulta registrada! ✅\nDr. Júlio — x")


@pytest.mark.asyncio
async def test_preview_pending_part2_adds_line():
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock,
               return_value=[{"id": "c1", "phone": "5581999998888", "name": "Carla"}]), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]):
        out = await pb.message_preview("p1", "c1", "taxa_isenta", ["Dr. Júlio — x"], pending_part2=True)
    assert "O horário da 2ª parte da primeira consulta será combinado depois." in out["text"]


def test_text_for_all_caps_contact_name_title_cased():
    txt = pb._text_for({"name": "CARLA MENEZES"}, "cortesia", ["Dr. Júlio — x"], False)
    assert txt.startswith("Perfeito, Carla!")


@pytest.mark.asyncio
async def test_send_skips_closed_window_and_writes_checkpoint_when_open():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"},
              {"id": "c2", "phone": "5581911112222", "name": "Paulo"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={"stage": "patient_agent"}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, side_effect=[True, False]), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.panel_booking.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.graph.graph.chatbot", chatbot):
        out = await pb.send_booking_message("p1", "c1", "normal", ["Dr. Júlio — x"], False, "Lucas", "julio")
    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][0] == "5581999998888@s.whatsapp.net"
    mock_save.assert_awaited_once()
    chatbot.aupdate_state.assert_awaited_once()
    cfg = chatbot.aupdate_state.call_args[0][0]
    assert cfg["configurable"]["thread_id"] == "5581999998888@s.whatsapp.net"
    assert out["sent"] == ["Carla"] and out["not_delivered"] == ["Paulo"]


@pytest.mark.asyncio
async def test_send_writes_pending_appointment_none_and_phone():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={"stage": "patient_agent"}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, return_value=True), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock), \
         patch("app.panel_booking.save_message", new_callable=AsyncMock), \
         patch("app.graph.graph.chatbot", chatbot):
        await pb.send_booking_message("p1", "c1", "normal", ["Dr. Júlio — x"], False, "Lucas Menezes", "julio")
    update = chatbot.aupdate_state.call_args[0][1]
    assert update["pending_appointment"] is None
    assert update["phone"] == "5581999998888@s.whatsapp.net"
    assert "stage" not in update  # estado não vazio: não re-semeia


@pytest.mark.asyncio
async def test_send_seeds_state_when_thread_is_empty():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, return_value=True), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock), \
         patch("app.panel_booking.save_message", new_callable=AsyncMock), \
         patch("app.graph.graph.chatbot", chatbot):
        await pb.send_booking_message("p1", "c1", "normal", ["Dr. Júlio — x"], False, "Lucas Menezes", "julio")
    update = chatbot.aupdate_state.call_args[0][1]
    assert update["pending_appointment"] is None
    assert update["phone"] == "5581999998888@s.whatsapp.net"
    assert update["stage"] == "patient_agent"
    assert update["user_name"] == "Lucas Menezes" and update["patient_name"] == "Lucas Menezes"
    assert update["is_patient"] is True
    assert update["preferred_doctor"] == "julio"


@pytest.mark.asyncio
async def test_send_text_failure_marks_not_delivered_and_skips_save():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, return_value=True), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock, side_effect=RuntimeError("wpp down")), \
         patch("app.panel_booking.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.graph.graph.chatbot", chatbot):
        out = await pb.send_booking_message("p1", "c1", "normal", ["Dr. Júlio — x"], False, "Lucas", "julio")
    mock_save.assert_not_awaited()
    chatbot.aupdate_state.assert_not_awaited()
    assert out["sent"] == [] and out["not_delivered"] == ["Carla"]


@pytest.mark.asyncio
async def test_send_saves_message_with_expected_text_and_role():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={"stage": "x"}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, return_value=True), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock), \
         patch("app.panel_booking.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.graph.graph.chatbot", chatbot):
        await pb.send_booking_message("p1", "c1", "normal", ["Dr. Júlio — x"], False, "Lucas", "julio")
    expected_text = pb._text_for({"name": "Carla"}, "normal", ["Dr. Júlio — x"], False)
    mock_save.assert_awaited_once_with("5581999998888@s.whatsapp.net", "assistant", expected_text)
    update = chatbot.aupdate_state.call_args[0][1]
    assert update["messages"][0].content == expected_text


@pytest.mark.asyncio
async def test_send_each_recipient_gets_own_name_in_text():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla Menezes"},
              {"id": "c2", "phone": "5581911112222", "name": "PAULO SILVA"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={"stage": "x"}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, return_value=True), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.panel_booking.save_message", new_callable=AsyncMock), \
         patch("app.graph.graph.chatbot", chatbot):
        await pb.send_booking_message("p1", "c1", "taxa_isenta", ["Dr. Júlio — x"], False, "Lucas", "julio")
    texts = [c.args[1] for c in mock_send.call_args_list]
    assert texts[0].startswith("Perfeito, Carla!")
    assert texts[1].startswith("Perfeito, Paulo!")


@pytest.mark.asyncio
async def test_notify_clinic_async_tracks_background_task():
    with patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        pb._notify_clinic_async("assunto", "corpo", "5581999998888@s.whatsapp.net")
        assert len(pb._background_tasks) == 1
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # done_callback roda no próximo tick após a task terminar
    mock_notify.assert_awaited_once()
    assert len(pb._background_tasks) == 0
