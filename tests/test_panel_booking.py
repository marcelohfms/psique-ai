from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import panel_booking as pb

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
    from contextlib import ExitStack
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client, **kw)]
        out = await pb.create_appointments(req)
    return out, mocks


@pytest.mark.asyncio
async def test_create_single_normal():
    client, q = _sb([])
    out, mocks = await _run(_req(), client)
    row = q.insert.call_args[0][0]
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
    row = q.insert.call_args[0][0]
    assert row["is_courtesy"] is True
    assert row["booking_fee_waived"] is True and row["booking_fee_paid_at"]
    assert out["kind"] == "cortesia"


@pytest.mark.asyncio
async def test_create_fee_waived():
    client, q = _sb([])
    out, _ = await _run(_req(billing="taxa_isenta"), client)
    row = q.insert.call_args[0][0]
    assert row["booking_fee_waived"] is True and row["is_courtesy"] is False
    assert out["kind"] == "taxa_isenta"


@pytest.mark.asyncio
async def test_create_minor_julio_non_first_sets_acompanhamento():
    """Menor com Dr. Júlio, sem marcar 1ª consulta, grava consultation_type=acompanhamento
    (regra usada depois por register_payment/pricing em app/graph/tools.py)."""
    client, q = _sb([])
    minor = {"id": "p1", "name": "LUCAS MENEZES", "social_name": None, "email": "l@x.com",
             "birth_date": "10/02/2016", "custom_price": None, "booking_fee_waived": False}
    out, _ = await _run(_req(patient=minor), client)
    row = q.insert.call_args[0][0]
    assert row["consultation_type"] == "acompanhamento"


@pytest.mark.asyncio
async def test_create_split_both_parts():
    client, q = _sb([])
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    out, mocks = await _run(_req(split=True, first_consultation=True,
                                 parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}]), client)
    rows = [c[0][0] for c in q.insert.call_args_list]
    assert [r["session_note"] for r in rows] == ["1ª consulta · parte 1 de 2", "1ª consulta · parte 2 de 2"]
    assert all(r["consultation_type"] == "primeira_consulta" for r in rows)
    assert mocks[2].await_count == 2
    assert len(out["appointments"]) == 2


@pytest.mark.asyncio
async def test_complete_pending_part2_inherits_fee():
    client, q = _sb([])
    part1 = {"appointment_id": "evtP1", "booking_fee_paid_at": "2026-09-30T10:00:00-03:00",
             "booking_fee_waived": False, "is_courtesy": False}
    out, _ = await _run(_req(split_of=part1, first_consultation=True), client)
    row = q.insert.call_args[0][0]
    assert row["session_note"] == "1ª consulta · parte 2 de 2"
    assert row["booking_fee_paid_at"] == "2026-09-30T10:00:00-03:00"
    assert out["kind"] == "taxa_paga"


@pytest.mark.asyncio
async def test_rollback_deletes_all_events_on_db_failure():
    client, q = _sb([])
    q.execute = AsyncMock(side_effect=RuntimeError("db down"))
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    with pytest.raises(pb.BookingError):
        await _run(_req(split=True, first_consultation=True,
                        parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}]), client)


@pytest.mark.asyncio
async def test_rollback_calls_cancel_event():
    client, q = _sb([])
    q.execute = AsyncMock(side_effect=RuntimeError("db down"))
    from contextlib import ExitStack
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client)]
        with pytest.raises(pb.BookingError):
            await pb.create_appointments(_req())
    mocks[3].assert_awaited_once_with("cal", "evt1")
