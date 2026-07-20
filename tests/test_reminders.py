import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

import scripts.send_appointment_reminders as rem

TZ = ZoneInfo("America/Recife")


def _appt(**kw):
    base = {
        "appointment_id": "evt-abc",
        "start_time": "2026-06-20T14:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "modality": "presencial",
        "patient_id": "p-joao",
        "patients": {"name": "João Silva"},
    }
    base.update(kw)
    return base


def _client():
    execute = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("update", "eq"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


@pytest.mark.asyncio
async def test_reminder_sent_to_all_agendamento_contacts():
    # pai e mãe ambos com role agendamento -> ambos recebem; marca sent_col 1x
    client, table = _client()
    contacts = [{"phone": "5581111"}, {"phone": "5581222"}]
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)
    with patch("scripts.send_appointment_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock) as mock_send:
        sent = await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    assert set(sent) == {"5581111", "5581222"}
    assert mock_send.await_count == 2
    table.update.assert_called_once()  # sent_col marcado uma vez por agendamento


@pytest.mark.asyncio
async def test_reminder_includes_inactive_contacts():
    # Regression: contato pausado (ex.: transferido p/ atendimento humano) não
    # pode silenciar o lembrete de consulta — é transacional, não conversacional.
    client, table = _client()
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)
    with patch("scripts.send_appointment_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]) as mock_gcfp, \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock):
        await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    mock_gcfp.assert_awaited_once_with("p-joao", "consulta", include_inactive=True)


@pytest.mark.asyncio
async def test_reminder_skips_when_no_agendamento_contact():
    # sem contato agendamento -> não envia nem marca (apenas loga e pula)
    client, table = _client()
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)
    with patch("scripts.send_appointment_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[]), \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock) as mock_send:
        sent = await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    assert sent == []
    mock_send.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_marks_sent_if_at_least_one_succeeds():
    # se um contato falha mas outro envia, ainda marca sent_col (retry não duplica)
    client, table = _client()
    contacts = [{"phone": "5581111"}, {"phone": "5581222"}]
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)

    async def flaky(phone, *a, **k):
        if phone == "5581111":
            raise RuntimeError("falha transitória")

    with patch("scripts.send_appointment_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               side_effect=flaky):
        sent = await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    assert sent == ["5581222"]
    table.update.assert_called_once()


@pytest.mark.asyncio
async def test_day_of_query_selects_patient_id():
    # Regression: a query "day-of" já esqueceu de selecionar patient_id e usava
    # join com a tabela antiga `users(...)`, fazendo get_contacts_for_patient
    # ser sempre chamado com patient_id=None e todo lembrete do dia ser pulado.
    table = MagicMock()
    for m in ("select", "eq", "is_", "gt", "gte", "lt", "lte"):
        getattr(table, m).return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False), \
         patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        await rem.main()

    select_calls = [c.args[0] for c in table.select.call_args_list]
    assert any("patient_id" in call and "patients(" in call for call in select_calls), select_calls
