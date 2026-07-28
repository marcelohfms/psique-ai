import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import scripts.complete_appointments as ca

NOW_ISO = "2026-07-23T19:34:58+00:00"


def _appt(**kw):
    base = {
        "id": "row-1",
        "appointment_id": "evt-abc",
        "patient_id": "p-natalia",
        "patients": {"name": "Natalia Pimentel"},
        "confirmed_at": None,
        "reminder_day_before_sent_at": None,
    }
    base.update(kw)
    return base


def _client(future_data=None):
    execute = AsyncMock(return_value=MagicMock(data=future_data or []))
    table = MagicMock()
    for m in ("select", "update", "eq", "gt", "limit"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


@pytest.mark.asyncio
async def test_sends_pos_consulta_for_same_day_booking_without_confirmation():
    # Regression (Natalia, 5581996332827): appointment booked and held the
    # same day never gets a day-before reminder, so confirmed_at is always
    # null even though the patient attended. Must NOT be treated as a no-show.
    client, table = _client()
    with patch("scripts.complete_appointments.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[{"phone": "5581996332827"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_send.assert_awaited_once_with("5581996332827", "Natalia")
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_skips_when_day_before_reminder_sent_and_never_confirmed():
    # Real no-show/cancel signal preserved: a day-before reminder DID ask for
    # confirmation and the patient never replied.
    client, table = _client()
    appt = _appt(reminder_day_before_sent_at="2026-07-21T10:00:00+00:00")
    with patch("scripts.complete_appointments.get_contacts_for_patient",
               new_callable=AsyncMock) as mock_gcfp, \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, appt, NOW_ISO)
    mock_gcfp.assert_not_awaited()
    mock_send.assert_not_awaited()
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_sends_when_confirmed_regardless_of_reminder():
    client, table = _client()
    appt = _appt(reminder_day_before_sent_at="2026-07-21T10:00:00+00:00",
                 confirmed_at="2026-07-21T12:00:00+00:00")
    with patch("scripts.complete_appointments.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, appt, NOW_ISO)
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_future_appointment_exists():
    client, table = _client(future_data=[{"id": "row-2"}])
    with patch("scripts.complete_appointments.get_contacts_for_patient",
               new_callable=AsyncMock) as mock_gcfp, \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_gcfp.assert_not_awaited()
    mock_send.assert_not_awaited()
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_skips_when_no_consulta_contact():
    client, table = _client()
    with patch("scripts.complete_appointments.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_send.assert_not_awaited()
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


def test_should_skip_unconfirmed():
    assert ca._should_skip_unconfirmed(
        {"reminder_day_before_sent_at": "2026-07-21T10:00:00+00:00", "confirmed_at": None}
    )
    assert not ca._should_skip_unconfirmed(
        {"reminder_day_before_sent_at": None, "confirmed_at": None}
    )
    assert not ca._should_skip_unconfirmed(
        {"reminder_day_before_sent_at": "2026-07-21T10:00:00+00:00", "confirmed_at": "2026-07-21T12:00:00+00:00"}
    )
