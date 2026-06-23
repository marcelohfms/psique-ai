import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app import patients


def _client_returning(rows):
    execute = AsyncMock(return_value=MagicMock(data=rows))
    table = MagicMock()
    for m in ("select", "eq", "insert", "update", "in_", "limit", "maybe_single", "order"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


def test_normalize_phone_adds_ninth_digit():
    assert patients.normalize_phone("5583988887777@s.whatsapp.net") == "5583988887777"
    assert patients.normalize_phone("558388887777") == "5583988887777"


@pytest.mark.asyncio
async def test_get_contact_by_phone_returns_row():
    client, table, execute = _client_returning([{"id": "c1", "phone": "5583988887777"}])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        contact = await patients.get_contact_by_phone("5583988887777@s.whatsapp.net")
    assert contact["id"] == "c1"
    table.eq.assert_called_with("phone", "5583988887777")


@pytest.mark.asyncio
async def test_get_contact_by_phone_returns_none_when_absent():
    client, table, execute = _client_returning([])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        contact = await patients.get_contact_by_phone("5583988887777")
    assert contact is None
