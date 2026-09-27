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
