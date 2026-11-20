import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import scripts.release_pending_reschedules as rel


def _fake_client(appts):
    table = MagicMock()
    for m in ("select", "eq", "is_", "lte", "gte", "order", "limit", "update"):
        getattr(table, m).return_value = table
    table.not_ = table  # so .not_.is_(...) chains back to table
    table.execute = AsyncMock(return_value=MagicMock(data=appts))
    client = MagicMock()
    client.from_.return_value = table
    return client, table


def test_release_pula_paciente_em_manual_hold(monkeypatch):
    monkeypatch.setattr(rel, "WINDOW_START", 0)
    monkeypatch.setattr(rel, "WINDOW_END", 24)
    monkeypatch.setenv("SUPABASE_URL", "http://fake")
    monkeypatch.setenv("SUPABASE_KEY", "fake")

    appt = {"appointment_id": "a1", "start_time": "2099-01-01T12:00:00+00:00",
            "doctor_id": "d1", "reschedule_requested_at": "2020-01-01T00:00:00+00:00",
            "users": {"number": "5581999", "name": "Fulano", "patient_name": "Fulano"}}
    client, table = _fake_client([appt])

    send = AsyncMock()
    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch.object(rel, "get_contact_by_phone", new_callable=AsyncMock,
                      return_value={"manual_hold": True}), \
         patch.object(rel, "send_whatsapp", send):
        asyncio.run(rel.main())

    send.assert_not_awaited()
    # slot não liberado → status nunca atualizado
    table.update.assert_not_called()


def _run_release(monkeypatch, *, window_open, send_template=None):
    monkeypatch.setattr(rel, "WINDOW_START", 0)
    monkeypatch.setattr(rel, "WINDOW_END", 24)
    monkeypatch.setenv("SUPABASE_URL", "http://fake")
    monkeypatch.setenv("SUPABASE_KEY", "fake")

    appt = {"appointment_id": "a1", "start_time": "2099-01-01T12:00:00+00:00",
            "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
            "reschedule_requested_at": "2020-01-01T00:00:00+00:00",
            "users": {"number": "5581999", "name": "Maria Silva", "patient_name": "Joao Silva"}}
    client, table = _fake_client([appt])
    # sem mensagem recente do paciente
    sb_table = MagicMock()
    for m in ("select", "eq", "order", "limit"):
        getattr(sb_table, m).return_value = sb_table
    sb_table.execute = AsyncMock(return_value=MagicMock(data=[]))
    sb = MagicMock()
    sb.from_.return_value = sb_table

    send = AsyncMock()
    tmpl = send_template or AsyncMock()
    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=sb), \
         patch("app.database.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch.object(rel, "get_contact_by_phone", new_callable=AsyncMock, return_value={}), \
         patch.object(rel, "_window_open", new_callable=AsyncMock, return_value=window_open), \
         patch.object(rel, "_send_template", tmpl), \
         patch.object(rel, "cancel_calendar_event", new_callable=AsyncMock), \
         patch.object(rel, "send_whatsapp", send):
        asyncio.run(rel.main())
    return send, tmpl, table


def test_release_closed_window_sends_template_and_releases(monkeypatch):
    send, tmpl, table = _run_release(monkeypatch, window_open=False)

    send.assert_not_awaited()
    tmpl.assert_awaited_once()
    args = tmpl.await_args.args
    assert args[0] == "5581999"
    assert args[1] == "vaga_liberada_remarcacao"
    assert args[2] == {"1": "Maria Silva", "2": "Joao Silva", "3": "Dr. Júlio"}
    assert "liberamos a vaga" in args[3]
    assert table.update.call_args.args[0]["status"] == "pending_reschedule"


def test_release_open_window_sends_free_text(monkeypatch):
    send, tmpl, table = _run_release(monkeypatch, window_open=True)

    send.assert_awaited_once()
    tmpl.assert_not_awaited()
    assert table.update.call_args.args[0]["status"] == "pending_reschedule"


def test_release_template_failure_does_not_release_slot(monkeypatch):
    tmpl = AsyncMock(side_effect=RuntimeError("meta recusou"))
    send, tmpl, table = _run_release(monkeypatch, window_open=False, send_template=tmpl)

    tmpl.assert_awaited_once()
    table.update.assert_not_called()
