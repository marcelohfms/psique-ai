import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import scripts.send_return_reminders as ret


@pytest.mark.asyncio
async def test_return_row_uses_return_reminder_contacts():
    client = MagicMock()
    row = {"id": "r1", "patient_id": "p1", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
           "patients": {"name": "Fulano"}}
    with patch("scripts.send_return_reminders.return_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581999", "name": "Mãe"}]) as sel, \
         patch("scripts.send_return_reminders._is_stale_classification",
               new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as send, \
         patch.object(client, "from_") as from_:
        upd = MagicMock(); upd.update.return_value = upd; upd.eq.return_value = upd
        upd.execute = AsyncMock(return_value=MagicMock(data=[]))
        from_.return_value = upd
        await ret._send_for_row(client, row, "retorno_no_mes", "month_of_sent_at", None)
    sel.assert_awaited_once_with("p1", include_inactive=True)
    send.assert_awaited_once()
