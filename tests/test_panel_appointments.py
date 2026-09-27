from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import panel_appointments as pa
from app.panel_booking import BookingError, PanelInputError

TZ = ZoneInfo("America/Recife")
JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
BRUNA = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
NOW = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)

ROW = {
    "id": "uuid-1", "appointment_id": "evt1", "patient_id": "p1", "contact_id": "c1",
    "doctor_id": JULIO, "status": "scheduled", "modality": "presencial",
    "start_time": "2026-10-05T12:00:00+00:00", "end_time": "2026-10-05T13:00:00+00:00",
    "consultation_type": None, "session_note": None, "booking_fee_paid_at": None,
    "booking_fee_waived": False, "is_courtesy": False, "refund_requested_at": None,
}
ADULT = {"id": "p1", "name": "Ana Souza", "birth_date": "01/01/1990", "custom_price": None,
         "modality_restriction": None, "social_name": None, "email": "ana@x.com", "patient_cpf": "123"}
KID = {**ADULT, "name": "Lucas Menezes", "birth_date": "01/01/2018"}
EDIT = {"phone": "5581999998888", "appointment_id": "evt1", "doctor": "julio", "modality": "presencial",
        "start": "2026-10-06T09:00", "minutes": 60, "session_note": "", "first_consultation": False,
        "billing": "normal", "initiated_by": "clinic", "agent": "Maria"}


def load_patches(row=None, patient=None, linked_ids=("c1",), sibling_rows=()):
    """Tudo que _load precisa, com relógio fixo em NOW."""
    return [
        patch("app.panel_appointments._now", return_value=NOW),
        patch("app.panel_appointments.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "c1"}),
        patch("app.panel_appointments._fetch_row", new_callable=AsyncMock, return_value=row if row is not None else ROW),
        patch("app.panel_appointments._linked_contacts_with_marker", new_callable=AsyncMock,
              return_value=[{"contact": {"id": cid}} for cid in linked_ids]),
        patch("app.panel_appointments.get_patient_by_id", new_callable=AsyncMock, return_value=patient or ADULT),
        patch("app.panel_appointments._fetch_active_rows", new_callable=AsyncMock, return_value=list(sibling_rows)),
    ]


async def build_edit(body=None, **kw):
    with ExitStack() as st:
        for p in load_patches(**kw):
            st.enter_context(p)
        return await pa.build_edit({**EDIT, **(body or {})})


# ── cobrança ────────────────────────────────────────────────────────────────

def test_current_billing():
    assert pa.current_billing(ROW, ADULT) == "normal"
    assert pa.current_billing({**ROW, "booking_fee_waived": True}, ADULT) == "taxa_isenta"
    assert pa.current_billing({**ROW, "is_courtesy": True, "booking_fee_waived": True}, ADULT) == "cortesia"
    assert pa.current_billing(ROW, {**ADULT, "custom_price": 0}) == "cortesia"


def test_fee_really_paid_ignores_waiver_timestamp():
    assert pa.fee_really_paid({**ROW, "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}, ADULT)
    assert not pa.fee_really_paid({**ROW, "booking_fee_paid_at": "x", "booking_fee_waived": True}, ADULT)


def test_billing_update_back_to_normal_clears_artificial_paid_at():
    row = {**ROW, "booking_fee_waived": True, "booking_fee_paid_at": "x"}
    assert pa.billing_update(row, ADULT, "normal", "now") == {
        "is_courtesy": False, "booking_fee_waived": False, "booking_fee_paid_at": None}


def test_billing_update_to_courtesy_keeps_real_payment_date():
    row = {**ROW, "booking_fee_paid_at": "paid"}
    assert pa.billing_update(row, ADULT, "cortesia", "now") == {
        "is_courtesy": True, "booking_fee_waived": True, "booking_fee_paid_at": "paid"}


def test_billing_update_same_billing_is_empty():
    assert pa.billing_update(ROW, ADULT, "normal", "now") == {}


# ── _load / build_edit ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_edit_requires_initiated_by():
    with pytest.raises(PanelInputError, match="quem pediu"):
        await build_edit({"initiated_by": None})


@pytest.mark.asyncio
async def test_build_edit_rejects_appointment_of_other_contact():
    with pytest.raises(PanelInputError, match="não pertence"):
        await build_edit(linked_ids=("c9",))


@pytest.mark.asyncio
async def test_build_edit_rejects_canceled_appointment():
    with pytest.raises(PanelInputError, match="não encontrada"):
        await build_edit(row={**ROW, "status": "canceled"})


@pytest.mark.asyncio
async def test_build_edit_nothing_changed():
    with pytest.raises(PanelInputError, match="nada mudou"):
        await build_edit({"start": "2026-10-05T09:00"})


@pytest.mark.asyncio
async def test_build_edit_new_time_in_past():
    with pytest.raises(PanelInputError, match="já passou"):
        await build_edit({"start": "2026-09-30T09:00"})


@pytest.mark.asyncio
async def test_build_edit_40min_only_bruna():
    with pytest.raises(PanelInputError, match="duração"):
        await build_edit({"minutes": 40})


@pytest.mark.asyncio
async def test_build_edit_time_change_notifies():
    req = await build_edit()
    assert req["changes"] == {"start"}
    assert req["notify"] is True and req["late_fee"] is False
    assert req["new"]["start"] == datetime(2026, 10, 6, 9, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_build_edit_note_only_does_not_notify():
    req = await build_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"})
    assert req["changes"] == {"note"} and req["notify"] is False


@pytest.mark.asyncio
async def test_build_edit_keeps_split_label_and_forces_first():
    row = {**ROW, "session_note": "1ª consulta · parte 1 de 2", "consultation_type": "primeira_consulta"}
    req = await build_edit({"session_note": "Domiciliar"}, row=row, patient=KID)
    assert req["new"]["note"] == "1ª consulta · parte 1 de 2 · Domiciliar"
    assert req["new"]["ctype"] == "primeira_consulta"


@pytest.mark.asyncio
async def test_build_edit_split_part_cannot_change_doctor():
    row = {**ROW, "session_note": "1ª consulta · parte 2 de 2"}
    with pytest.raises(PanelInputError, match="dividida"):
        await build_edit({"doctor": "bruna"}, row=row, patient=KID)


@pytest.mark.asyncio
async def test_build_edit_minor_julio_first_flag():
    req = await build_edit({"start": "2026-10-05T09:00", "first_consultation": True}, patient=KID)
    assert req["new"]["ctype"] == "primeira_consulta" and req["changes"] == {"consultation_type"}


@pytest.mark.asyncio
async def test_build_edit_late_fee_patient_under_24h_with_paid_fee():
    row = {**ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00",
           "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}
    req = await build_edit({"initiated_by": "patient"}, row=row)
    assert req["late_fee"] is True


@pytest.mark.asyncio
async def test_build_edit_no_late_fee_when_clinic_or_waived():
    row = {**ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00",
           "booking_fee_paid_at": "x"}
    assert (await build_edit({"initiated_by": "clinic"}, row=row))["late_fee"] is False
    waived = {**row, "booking_fee_waived": True}
    assert (await build_edit({"initiated_by": "patient", "billing": "taxa_isenta"}, row=waived))["late_fee"] is False


@pytest.mark.asyncio
async def test_build_edit_modality_restriction():
    with pytest.raises(PanelInputError, match="só pode ser atendido"):
        await build_edit({"modality": "online"}, patient={**ADULT, "modality_restriction": "presencial"})


@pytest.mark.asyncio
async def test_build_edit_custom_price_zero_stays_courtesy():
    row = {**ROW, "is_courtesy": True, "booking_fee_waived": True, "booking_fee_paid_at": "x"}
    req = await build_edit({"billing": "normal"}, row=row, patient={**ADULT, "custom_price": 0})
    assert req["new"]["billing"] == "cortesia" and "billing" not in req["changes"]


# ── apply_edit ─────────────────────────────────────────────────────────────

def cal_patches(update_row=None):
    cal = {
        "cal_id": patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock,
                        side_effect=lambda d: f"cal-{d}"),
        "update_event": patch("app.google_calendar.update_event", new_callable=AsyncMock),
        "create_event": patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-new"),
        "cancel_event": patch("app.google_calendar.cancel_event", new_callable=AsyncMock),
        "update_row": patch("app.panel_appointments._update_row", new_callable=AsyncMock, side_effect=update_row),
        "log_event": patch("app.panel_appointments.log_event", new_callable=AsyncMock),
        "notify": patch("app.panel_appointments._notify_clinic_async"),
    }
    return cal


async def run_apply(req, update_row=None):
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(update_row).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        out = await pa.apply_edit(req)
    return out, m


@pytest.mark.asyncio
async def test_apply_edit_same_doctor_updates_event_and_resets_confirmations():
    req = await build_edit()
    out, m = await run_apply(req)
    m["update_event"].assert_awaited_once()
    kw = m["update_event"].call_args.kwargs
    assert kw["calendar_id"] == "cal-julio" and kw["event_id"] == "evt1"
    assert kw["new_start"] == datetime(2026, 10, 6, 9, 0, tzinfo=TZ)
    row_id, fields = m["update_row"].call_args[0]
    assert row_id == "uuid-1"
    assert fields["confirmed_at"] is None and fields["reminder_day_before_sent_at"] is None
    assert fields["reminder_day_of_sent_at"] is None and fields["reschedule_initiated_by"] == "clinic"
    assert out["appointment_id"] == "evt1"
    logged = [c[0][0] for c in m["log_event"].call_args_list]
    assert logged == ["appointment_edited", "appointment_rescheduled"]
    m["notify"].assert_called_once()


@pytest.mark.asyncio
async def test_apply_edit_note_only_keeps_confirmation_and_skips_email():
    req = await build_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"})
    out, m = await run_apply(req)
    fields = m["update_row"].call_args[0][1]
    assert "confirmed_at" not in fields and fields["session_note"] == "Domiciliar"
    assert m["update_event"].call_args.kwargs["session_note"] == "Domiciliar"
    assert [c[0][0] for c in m["log_event"].call_args_list] == ["appointment_edited"]
    m["notify"].assert_not_called()


@pytest.mark.asyncio
async def test_apply_edit_doctor_change_creates_new_event_then_deletes_old():
    req = await build_edit({"doctor": "bruna", "start": "2026-10-05T09:00"})
    out, m = await run_apply(req)
    assert m["create_event"].call_args.kwargs["calendar_id"] == "cal-bruna"
    fields = m["update_row"].call_args[0][1]
    assert fields["appointment_id"] == "evt-new" and fields["doctor_id"] == BRUNA
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["appointment_id"] == "evt-new"


@pytest.mark.asyncio
async def test_apply_edit_pending_reschedule_creates_event_and_schedules():
    req = await build_edit(row={**ROW, "status": "pending_reschedule"})
    out, m = await run_apply(req)
    m["create_event"].assert_awaited_once()
    m["update_event"].assert_not_called()
    fields = m["update_row"].call_args[0][1]
    assert fields["status"] == "scheduled" and fields["reschedule_requested_at"] is None
    m["cancel_event"].assert_not_called()


@pytest.mark.asyncio
async def test_apply_edit_db_failure_reverts_calendar_update():
    req = await build_edit()
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError):
            await pa.apply_edit(req)
    assert m["update_event"].await_count == 2
    assert m["update_event"].call_args.kwargs["new_start"] == datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_apply_edit_db_failure_deletes_created_event():
    req = await build_edit({"doctor": "bruna", "start": "2026-10-05T09:00"})
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError):
            await pa.apply_edit(req)
    m["cancel_event"].assert_awaited_once_with("cal-bruna", "evt-new")


@pytest.mark.asyncio
async def test_apply_edit_calendar_failure_changes_nothing():
    req = await build_edit()
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches().items()}
        m["update_event"].side_effect = RuntimeError("gcal")
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError, match="nada foi alterado"):
            await pa.apply_edit(req)
    m["update_row"].assert_not_called()


# ── remarcação tardia ──────────────────────────────────────────────────────

LATE_ROW = {**ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00",
            "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}


@pytest.mark.asyncio
async def test_late_reschedule_cancels_old_row_and_creates_new_normal_fee():
    req = await build_edit({"initiated_by": "patient"}, row=LATE_ROW)
    created = {"kind": "normal", "lines": ["l"], "pending_part2": False,
               "appointments": [{"appointment_id": "evt-new"}]}
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches().items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        mock_create = st.enter_context(patch("app.panel_appointments.create_appointments",
                                             new_callable=AsyncMock, return_value=created))
        out = await pa.apply_late_reschedule(req)
    booking = mock_create.call_args[0][0]
    assert booking["billing"] == "normal" and booking["contact_id"] == "c1"
    assert booking["parts"] == [{"start": datetime(2026, 10, 6, 9, 0, tzinfo=TZ), "minutes": 60}]
    assert m["update_row"].call_args[0] == ("uuid-1", {"status": "canceled", "updated_at": NOW.isoformat()})
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["appointment_id"] == "evt-new" and out["new_fee"] is True


@pytest.mark.asyncio
async def test_late_reschedule_old_row_failure_rolls_back_new_one():
    req = await build_edit({"initiated_by": "patient"}, row=LATE_ROW)
    created = {"kind": "normal", "lines": ["l"], "pending_part2": False,
               "appointments": [{"appointment_id": "evt-new"}]}
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        st.enter_context(patch("app.panel_appointments.create_appointments", new_callable=AsyncMock, return_value=created))
        mock_del = st.enter_context(patch("app.panel_appointments._delete_row", new_callable=AsyncMock))
        with pytest.raises(BookingError):
            await pa.apply_late_reschedule(req)
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt-new")
    mock_del.assert_awaited_once_with("evt-new")


# ── handle_edit ────────────────────────────────────────────────────────────

async def run_handle_edit(body, check=(), row=None, patient=None, applied=None):
    with ExitStack() as st:
        for p in load_patches(row=row, patient=patient):
            st.enter_context(p)
        mock_check = st.enter_context(patch("app.panel_appointments.check_slot", new_callable=AsyncMock, return_value=list(check)))
        mock_prev = st.enter_context(patch("app.panel_appointments.preview_message", new_callable=AsyncMock,
                                           return_value={"text": "t", "recipients": [], "held": []}))
        mock_apply = st.enter_context(patch("app.panel_appointments.apply_edit", new_callable=AsyncMock,
                                            return_value=applied or {"appointment_id": "evt1", "old_line": "a",
                                                                     "new_line": "b", "new_fee": False, "warnings": []}))
        mock_late = st.enter_context(patch("app.panel_appointments.apply_late_reschedule", new_callable=AsyncMock,
                                           return_value={"appointment_id": "evt-new", "old_line": "a",
                                                         "new_line": "b", "new_fee": True, "warnings": []}))
        mock_send = st.enter_context(patch("app.panel_appointments.deliver_message", new_callable=AsyncMock,
                                           return_value={"sent": ["Ana"], "not_delivered": [], "held": []}))
        status, payload = await pa.handle_edit({**EDIT, **body})
    return status, payload, {"check": mock_check, "prev": mock_prev, "apply": mock_apply,
                             "late": mock_late, "send": mock_send}


@pytest.mark.asyncio
async def test_handle_edit_dry_run_reports_encaixe_and_late_fee():
    status, payload, m = await run_handle_edit({"dry_run": True, "initiated_by": "patient"},
                                               check=["dia bloqueado na agenda"], row=LATE_ROW)
    assert status == 200
    assert payload["encaixe_reasons"] == ["dia bloqueado na agenda"]
    assert payload["late_fee"] is True and payload["notify"] is True
    assert m["check"].call_args.kwargs["exclude_appointment_id"] == "evt1"
    m["apply"].assert_not_called()


@pytest.mark.asyncio
async def test_handle_edit_needs_encaixe_409():
    status, payload, m = await run_handle_edit({}, check=["fora do horário de atendimento"])
    assert status == 409 and payload["detail"]["needs_encaixe"] is True
    m["apply"].assert_not_called()


@pytest.mark.asyncio
async def test_handle_edit_encaixe_confirmed_skips_check():
    status, payload, m = await run_handle_edit({"encaixe_confirmed": True})
    assert status == 200
    m["check"].assert_not_called()
    assert m["apply"].call_args[0][0]["encaixe"] is True


@pytest.mark.asyncio
async def test_handle_edit_note_only_no_check_no_message():
    status, payload, m = await run_handle_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"})
    assert status == 200
    m["check"].assert_not_called()
    m["send"].assert_not_called()
    assert payload["message"] == {"sent": [], "not_delivered": [], "held": [], "skipped": True}


@pytest.mark.asyncio
async def test_handle_edit_late_goes_to_late_path_and_message_has_new_fee():
    status, payload, m = await run_handle_edit({"initiated_by": "patient"}, row=LATE_ROW)
    assert status == 200 and payload["appointment_id"] == "evt-new"
    m["late"].assert_awaited_once()
    m["apply"].assert_not_called()
    text_for = m["send"].call_args[0][2]
    assert "nova taxa" in text_for({"name": "Ana"})


# ── cancelar ───────────────────────────────────────────────────────────────

PAID_ROW = {**ROW, "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}   # consulta 05/10, NOW 01/10: > 24h
CANCEL = {"phone": "5581999998888", "appointment_id": "evt1", "initiated_by": "patient",
          "fee_action": None, "reason": "", "both_parts": False, "agent": "Maria"}


async def build_cancel(body=None, **kw):
    with ExitStack() as st:
        for p in load_patches(**kw):
            st.enter_context(p)
        return await pa.build_cancel({**CANCEL, **(body or {})})


@pytest.mark.asyncio
async def test_build_cancel_requires_initiated_by_unless_dry_run():
    with pytest.raises(PanelInputError, match="quem pediu"):
        await build_cancel({"initiated_by": None})
    req = await build_cancel({"initiated_by": None, "dry_run": True})
    assert req["initiated_by"] is None


@pytest.mark.asyncio
async def test_build_cancel_paid_fee_requires_choice():
    with pytest.raises(PanelInputError, match="taxa"):
        await build_cancel(row=PAID_ROW)
    req = await build_cancel({"fee_action": "devolver"}, row=PAID_ROW)
    assert req["fee_paid"] is True and req["policy_late"] is False


@pytest.mark.asyncio
async def test_build_cancel_fee_action_ignored_when_not_paid():
    req = await build_cancel({"fee_action": "devolver"})
    assert req["fee_paid"] is False and req["fee_action"] is None


@pytest.mark.asyncio
async def test_build_cancel_late_refund_needs_reason():
    late = {**PAID_ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00"}
    with pytest.raises(PanelInputError, match="motivo"):
        await build_cancel({"fee_action": "devolver"}, row=late)
    req = await build_cancel({"fee_action": "devolver", "reason": "exceção aprovada pelo Dr. Júlio"}, row=late)
    assert req["policy_late"] is True


@pytest.mark.asyncio
async def test_build_cancel_credit_not_allowed_on_pending_reschedule():
    with pytest.raises(PanelInputError, match="crédito"):
        await build_cancel({"fee_action": "credito"}, row={**PAID_ROW, "status": "pending_reschedule"})


@pytest.mark.asyncio
async def test_build_cancel_refund_already_requested():
    with pytest.raises(PanelInputError, match="já foi pedida"):
        await build_cancel({"fee_action": "devolver"}, row={**PAID_ROW, "refund_requested_at": "x"})


@pytest.mark.asyncio
async def test_build_cancel_finds_split_sibling():
    part1 = {**ROW, "session_note": "1ª consulta · parte 1 de 2"}
    part2 = {**ROW, "id": "uuid-2", "appointment_id": "evt2", "session_note": "1ª consulta · parte 2 de 2",
             "start_time": "2026-10-08T12:00:00+00:00", "end_time": "2026-10-08T13:00:00+00:00"}
    req = await build_cancel({"both_parts": True}, row=part1, patient=KID, sibling_rows=[part1, part2])
    assert req["sibling"]["appointment_id"] == "evt2" and req["both_parts"] is True


def cancel_patches(update_row=None, cancel_event=None, sheet=None):
    return {
        "cal_id": patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"),
        "cancel_event": patch("app.google_calendar.cancel_event", new_callable=AsyncMock, side_effect=cancel_event),
        "update_row": patch("app.panel_appointments._update_row", new_callable=AsyncMock, side_effect=update_row),
        "log_event": patch("app.panel_appointments.log_event", new_callable=AsyncMock),
        "notify": patch("app.panel_appointments._notify_clinic_async"),
        "sheet": patch("app.google_sheets.append_document_request", new_callable=AsyncMock, side_effect=sheet),
        "now": patch("app.panel_appointments._now", return_value=NOW),
    }


async def run_cancel(req, **kw):
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cancel_patches(**kw).items()}
        out = await pa.apply_cancel(req)
    return out, m


@pytest.mark.asyncio
async def test_apply_cancel_db_first_then_calendar():
    req = await build_cancel()
    out, m = await run_cancel(req)
    assert m["update_row"].call_args_list[0][0] == ("uuid-1", {"status": "canceled", "updated_at": NOW.isoformat()})
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["canceled"] == ["evt1"]
    assert m["log_event"].call_args[0][0] == "appointment_canceled"


@pytest.mark.asyncio
async def test_apply_cancel_calendar_failure_restores_status():
    req = await build_cancel()
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cancel_patches(cancel_event=RuntimeError("gcal")).items()}
        with pytest.raises(BookingError, match="nada foi alterado"):
            await pa.apply_cancel(req)
    assert m["update_row"].call_args_list[1][0][1]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_apply_cancel_event_already_gone_is_success():
    gone = Exception("gone")
    gone.resp = MagicMock(status=410)
    req = await build_cancel()
    out, m = await run_cancel(req, cancel_event=gone)
    assert out["canceled"] == ["evt1"]
    assert len(m["update_row"].call_args_list) == 1


@pytest.mark.asyncio
async def test_apply_cancel_credit_keeps_fee_as_pending_reschedule():
    req = await build_cancel({"fee_action": "credito"}, row=PAID_ROW)
    out, m = await run_cancel(req)
    assert m["update_row"].call_args_list[0][0][1]["status"] == "pending_reschedule"
    m["sheet"].assert_not_called()


@pytest.mark.asyncio
async def test_apply_cancel_refund_marks_and_writes_sheet():
    req = await build_cancel({"fee_action": "devolver", "reason": "mudou de cidade"}, row=PAID_ROW)
    out, m = await run_cancel(req)
    refund_fields = m["update_row"].call_args_list[1][0][1]
    assert refund_fields["refund_requested_at"] == NOW.isoformat()
    kw = m["sheet"].call_args.kwargs
    assert kw["document_type"] == "Solicitação de Reembolso"
    assert kw["medication_note"] == "Valor: R$ 100,00 | Consulta: 05/10/2026 às 09:00 | Motivo: mudou de cidade"
    assert kw["patient_cpf"] == "123"
    logged = [c[0][0] for c in m["log_event"].call_args_list]
    assert "refund_requested" in logged


@pytest.mark.asyncio
async def test_apply_cancel_sheet_failure_becomes_warning():
    req = await build_cancel({"fee_action": "devolver"}, row=PAID_ROW)
    out, m = await run_cancel(req, sheet=RuntimeError("sheets"))
    assert out["canceled"] == ["evt1"]
    assert any("planilha" in w for w in out["warnings"])


@pytest.mark.asyncio
async def test_apply_cancel_both_parts_refund_once():
    part1 = {**PAID_ROW, "session_note": "1ª consulta · parte 1 de 2"}
    part2 = {**PAID_ROW, "id": "uuid-2", "appointment_id": "evt2", "session_note": "1ª consulta · parte 2 de 2"}
    req = await build_cancel({"both_parts": True, "fee_action": "devolver"}, row=part1, patient=KID,
                             sibling_rows=[part1, part2])
    out, m = await run_cancel(req)
    assert out["canceled"] == ["evt1", "evt2"]
    m["sheet"].assert_awaited_once()


# ── handle_cancel ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_cancel_dry_run_info_without_initiated_by():
    with ExitStack() as st:
        for p in load_patches(row=PAID_ROW):
            st.enter_context(p)
        mock_prev = st.enter_context(patch("app.panel_appointments.preview_message", new_callable=AsyncMock))
        status, payload = await pa.handle_cancel({**CANCEL, "initiated_by": None, "dry_run": True})
    assert status == 200
    assert payload["fee_paid"] is True and payload["policy_late"] is False
    assert payload["status"] == "scheduled" and payload["sibling"] is None
    assert payload["message"] is None
    mock_prev.assert_not_called()


@pytest.mark.asyncio
async def test_handle_cancel_sends_message_with_fee_line():
    with ExitStack() as st:
        for p in load_patches(row=PAID_ROW):
            st.enter_context(p)
        st.enter_context(patch("app.panel_appointments.apply_cancel", new_callable=AsyncMock,
                               return_value={"canceled": ["evt1"], "lines": ["l1"], "warnings": []}))
        mock_send = st.enter_context(patch("app.panel_appointments.deliver_message", new_callable=AsyncMock,
                                           return_value={"sent": ["Ana"], "not_delivered": [], "held": []}))
        status, payload = await pa.handle_cancel({**CANCEL, "fee_action": "credito"})
    assert status == 200 and payload["canceled"] == ["evt1"]
    # "Ana Souza" ficaria inteiro (display_name mantém nomes compostos com Ana); use um nome simples.
    text = mock_send.call_args[0][2]({"name": "CARLA MENEZES"})
    assert text.startswith("Olá, Carla! Conforme combinado, sua consulta foi cancelada:\nl1")
    assert "fica guardado" in text


# ── ajustes da revisão ─────────────────────────────────────────────────────

PAID_ROW = {**ROW, "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}


@pytest.mark.asyncio
async def test_build_edit_refuses_waiving_really_paid_fee():
    for billing in ("taxa_isenta", "cortesia"):
        with pytest.raises(PanelInputError, match="já foi paga"):
            await build_edit({"billing": billing}, row=PAID_ROW)


@pytest.mark.asyncio
async def test_build_edit_paid_fee_custom_price_zero_still_courtesy():
    req = await build_edit({"billing": "normal"}, row=PAID_ROW, patient={**ADULT, "custom_price": 0})
    assert req["new"]["billing"] == "cortesia"


@pytest.mark.asyncio
async def test_apply_edit_keeps_confirmation_mark_when_time_unchanged():
    row = {**ROW, "confirmed_at": "2026-09-30T10:00:00-03:00"}
    req = await build_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"}, row=row)
    _, m = await run_apply(req)
    assert m["update_event"].call_args.kwargs["confirmed"] is True


@pytest.mark.asyncio
async def test_apply_edit_time_change_drops_confirmation_mark():
    row = {**ROW, "confirmed_at": "2026-09-30T10:00:00-03:00"}
    req = await build_edit(row=row)
    _, m = await run_apply(req)
    assert m["update_event"].call_args.kwargs["confirmed"] is False


@pytest.mark.asyncio
async def test_apply_edit_rollback_restores_confirmation_mark():
    row = {**ROW, "confirmed_at": "2026-09-30T10:00:00-03:00"}
    req = await build_edit(row=row)
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError):
            await pa.apply_edit(req)
    assert m["update_event"].call_args.kwargs["confirmed"] is True


def test_appt_cols_include_confirmed_at():
    assert "confirmed_at" in pa._APPT_COLS


@pytest.mark.asyncio
async def test_build_edit_pending_reschedule_same_past_time_refused():
    row = {**ROW, "status": "pending_reschedule", "start_time": "2026-09-30T12:00:00+00:00",
           "end_time": "2026-09-30T13:00:00+00:00"}
    with pytest.raises(PanelInputError, match="já passou"):
        await build_edit({"start": "2026-09-30T09:00"}, row=row)


@pytest.mark.asyncio
async def test_late_reschedule_notifies_clinic():
    req = await build_edit({"initiated_by": "patient"}, row=LATE_ROW)
    created = {"kind": "normal", "lines": ["l"], "pending_part2": False,
               "appointments": [{"appointment_id": "evt-new"}]}
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches().items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        st.enter_context(patch("app.panel_appointments.create_appointments", new_callable=AsyncMock, return_value=created))
        await pa.apply_late_reschedule(req)
    m["notify"].assert_called_once()
    subject, body, phone = m["notify"].call_args[0]
    assert subject == "Agendamento alterado — Ana Souza"
    assert "menos de 24h" in body and "A taxa anterior fica retida; nova taxa de reserva cobrada" in body
    assert "Atendente: Maria" in body


@pytest.mark.asyncio
async def test_late_reschedule_failure_does_not_notify():
    req = await build_edit({"initiated_by": "patient"}, row=LATE_ROW)
    created = {"kind": "normal", "lines": ["l"], "pending_part2": False,
               "appointments": [{"appointment_id": "evt-new"}]}
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        st.enter_context(patch("app.panel_appointments.create_appointments", new_callable=AsyncMock, return_value=created))
        st.enter_context(patch("app.panel_appointments._delete_row", new_callable=AsyncMock))
        with pytest.raises(BookingError):
            await pa.apply_late_reschedule(req)
    m["notify"].assert_not_called()


@pytest.mark.asyncio
async def test_build_edit_split_part_never_late_fee():
    row = {**LATE_ROW, "session_note": "1ª consulta · parte 1 de 2", "consultation_type": "primeira_consulta"}
    req = await build_edit({"initiated_by": "patient"}, row=row, patient=KID)
    assert req["late_fee"] is False


@pytest.mark.asyncio
async def test_build_edit_legacy_null_modality_note_only_does_not_notify():
    req = await build_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"},
                           row={**ROW, "modality": None})
    assert req["changes"] == {"note"} and req["notify"] is False


@pytest.mark.asyncio
async def test_apply_edit_doctor_only_change_not_logged_as_reschedule():
    req = await build_edit({"doctor": "bruna", "start": "2026-10-05T09:00"})
    _, m = await run_apply(req)
    assert [c[0][0] for c in m["log_event"].call_args_list] == ["appointment_edited"]
    fields = m["update_row"].call_args[0][1]
    assert fields["confirmed_at"] is None


# ── cancelar: ajustes da revisão ───────────────────────────────────────────

PART1 = {**PAID_ROW, "session_note": "1ª consulta · parte 1 de 2"}
PART2 = {**PAID_ROW, "id": "uuid-2", "appointment_id": "evt2", "session_note": "1ª consulta · parte 2 de 2",
         "start_time": "2026-10-08T12:00:00+00:00", "end_time": "2026-10-08T13:00:00+00:00"}


def _fail_on(event_id):
    async def side(cal, evt):
        if evt == event_id:
            raise RuntimeError("gcal")
    return side


@pytest.mark.asyncio
async def test_apply_cancel_sibling_failure_becomes_warning_and_uses_done_rows():
    req = await build_cancel({"both_parts": True, "fee_action": "devolver"}, row=PART1, patient=KID,
                             sibling_rows=[PART1, PART2])
    out, m = await run_cancel(req, cancel_event=_fail_on("evt2"))
    assert out["canceled"] == ["evt1"]
    assert len(out["lines"]) == 1 and "05/10" in out["lines"][0]
    assert any("outra parte" in w for w in out["warnings"])
    m["sheet"].assert_awaited_once()
    notice = m["notify"].call_args[0][1]
    assert "08/10" not in notice


@pytest.mark.asyncio
async def test_apply_cancel_first_row_failure_still_raises():
    req = await build_cancel({"both_parts": True, "fee_action": "devolver"}, row=PART1, patient=KID,
                             sibling_rows=[PART1, PART2])
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cancel_patches(cancel_event=_fail_on("evt1")).items()}
        with pytest.raises(BookingError):
            await pa.apply_cancel(req)
    m["sheet"].assert_not_called()


@pytest.mark.asyncio
async def test_build_cancel_pending_reschedule_is_never_late():
    pend = {**PAID_ROW, "status": "pending_reschedule",
            "start_time": "2026-09-20T12:00:00+00:00", "end_time": "2026-09-20T13:00:00+00:00"}
    req = await build_cancel({"fee_action": "devolver"}, row=pend)
    assert req["policy_late"] is False


@pytest.mark.asyncio
async def test_handle_cancel_dry_run_pending_reschedule_hours_none():
    pend = {**PAID_ROW, "status": "pending_reschedule",
            "start_time": "2026-09-20T12:00:00+00:00", "end_time": "2026-09-20T13:00:00+00:00"}
    with ExitStack() as st:
        for p in load_patches(row=pend):
            st.enter_context(p)
        status, payload = await pa.handle_cancel({**CANCEL, "initiated_by": None, "dry_run": True})
    assert status == 200
    assert payload["hours_until"] is None and payload["policy_late"] is False


@pytest.mark.asyncio
async def test_build_cancel_split_refund_requires_both_parts():
    with pytest.raises(PanelInputError, match="cancele as duas"):
        await build_cancel({"fee_action": "devolver"}, row=PART1, patient=KID, sibling_rows=[PART1, PART2])
    req = await build_cancel({"fee_action": "credito"}, row=PART1, patient=KID, sibling_rows=[PART1, PART2])
    assert req["both_parts"] is False and req["fee_action"] == "credito"


@pytest.mark.asyncio
async def test_build_cancel_past_scheduled_row_rejected():
    past = {**ROW, "start_time": "2026-09-30T12:00:00+00:00", "end_time": "2026-09-30T13:00:00+00:00"}
    with pytest.raises(PanelInputError, match="já passou"):
        await build_cancel(row=past)
    with pytest.raises(PanelInputError, match="já passou"):
        await build_cancel({"dry_run": True}, row=past)


@pytest.mark.asyncio
async def test_apply_cancel_pending_reschedule_row_skips_calendar():
    pend = {**PAID_ROW, "status": "pending_reschedule"}
    req = await build_cancel({"fee_action": "reter"}, row=pend)
    out, m = await run_cancel(req)
    m["cancel_event"].assert_not_called()
    assert out["canceled"] == ["evt1"]
    assert m["update_row"].call_args_list[0][0][1]["status"] == "canceled"


@pytest.mark.asyncio
async def test_apply_cancel_pair_with_pending_sibling_only_scheduled_hits_calendar():
    sib = {**PART2, "status": "pending_reschedule"}
    req = await build_cancel({"both_parts": True, "fee_action": "reter"}, row=PART1, patient=KID,
                             sibling_rows=[PART1, sib])
    out, m = await run_cancel(req)
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["canceled"] == ["evt1", "evt2"]


@pytest.mark.asyncio
async def test_handle_cancel_reter_text_does_not_mention_fee():
    with ExitStack() as st:
        for p in load_patches(row=PAID_ROW):
            st.enter_context(p)
        st.enter_context(patch("app.panel_appointments.apply_cancel", new_callable=AsyncMock,
                               return_value={"canceled": ["evt1"], "lines": ["l1"], "warnings": []}))
        mock_send = st.enter_context(patch("app.panel_appointments.deliver_message", new_callable=AsyncMock,
                                           return_value={"sent": ["Carla"], "not_delivered": [], "held": []}))
        await pa.handle_cancel({**CANCEL, "fee_action": "reter"})
    text = mock_send.call_args[0][2]({"name": "CARLA MENEZES"})
    assert "taxa" not in text.lower()
