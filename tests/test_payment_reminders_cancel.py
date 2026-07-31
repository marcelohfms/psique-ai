import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

import scripts.send_payment_reminders as spr

TZ = ZoneInfo("America/Recife")


def _appt(**kw):
    base = {
        "appointment_id": "evt-abc",
        "start_time": "2026-08-03T17:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "patient_id": "p-joao",
        "patients": {"name": "João Pedro"},
    }
    base.update(kw)
    return base


def _client():
    execute = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("update", "eq", "select", "single"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


@pytest.mark.asyncio
async def test_cancel_logs_appointment_canceled_event_per_notified_contact():
    """Toda vez que o cron cancela uma consulta por falta de pagamento, precisa
    registrar um evento appointment_canceled em `events` — igual a todo outro
    caminho de cancelamento (cancel_appointment em app/graph/tools.py). Sem isso, a
    tabela `events` não mostra nenhum rastro do cancelamento, e investigar um caso
    exige cruzar Chatwoot e Google Calendar na mão em vez de ler uma tabela (caso
    João Pedro Lins Da Costa Gomes, 5581992349207, 2026-07-30)."""
    client, table = _client()
    now = datetime(2026, 7, 29, 12, 26, tzinfo=TZ)
    appt = _appt()

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581992349207", "name": "Ednara"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_log_event.assert_awaited_once_with("appointment_canceled", "5581992349207", {
        "appointment_id": "evt-abc",
        "reason": "unpaid_booking_fee",
        "start_time": "2026-08-03T17:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
    })


@pytest.mark.asyncio
async def test_cancel_does_not_log_event_when_no_contact_notified():
    """Se nenhum contato financeiro foi notificado com sucesso, o cancelamento é
    adiado (comportamento existente) — e portanto nenhum evento deve ser
    registrado, já que a consulta continua scheduled."""
    client, table = _client()
    now = datetime(2026, 7, 29, 12, 26, tzinfo=TZ)
    appt = _appt()

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581992349207", "name": "Ednara"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp",
               new_callable=AsyncMock, side_effect=Exception("WhatsApp down")), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event:
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_log_event.assert_not_awaited()
    table.update.assert_not_called()
