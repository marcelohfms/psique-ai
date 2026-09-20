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
