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
