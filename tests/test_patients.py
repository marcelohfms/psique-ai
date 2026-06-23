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


@pytest.mark.asyncio
async def test_get_patients_by_contact_filters_by_role():
    client, table, execute = _client_returning([
        {"patient_id": "p1", "role": "agendamento", "is_self": True,
         "patients": {"id": "p1", "name": "João"}},
    ])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        result = await patients.get_patients_by_contact("c1", role="agendamento")
    assert result == [{"id": "p1", "name": "João"}]
    table.eq.assert_any_call("contact_id", "c1")
    table.eq.assert_any_call("role", "agendamento")


@pytest.mark.asyncio
async def test_get_patients_by_contact_without_role_returns_all():
    client, table, execute = _client_returning([
        {"patient_id": "p1", "role": "agendamento", "is_self": False,
         "patients": {"id": "p1", "name": "João"}},
        {"patient_id": "p2", "role": "financeiro", "is_self": False,
         "patients": {"id": "p2", "name": "Maria"}},
    ])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        result = await patients.get_patients_by_contact("c1")
    assert {p["id"] for p in result} == {"p1", "p2"}


@pytest.mark.asyncio
async def test_get_contacts_for_patient_returns_all_agendamento_contacts():
    client, table, execute = _client_returning([
        {"contact_id": "cpai", "contacts": {"id": "cpai", "phone": "5583111", "active": True}},
        {"contact_id": "cmae", "contacts": {"id": "cmae", "phone": "5583222", "active": True}},
    ])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        result = await patients.get_contacts_for_patient("p1", role="agendamento")
    assert {c["phone"] for c in result} == {"5583111", "5583222"}
    table.eq.assert_any_call("patient_id", "p1")
    table.eq.assert_any_call("role", "agendamento")


@pytest.mark.asyncio
async def test_get_contacts_for_patient_skips_inactive():
    client, table, execute = _client_returning([
        {"contact_id": "cpai", "contacts": {"id": "cpai", "phone": "5583111", "active": True}},
        {"contact_id": "cold", "contacts": {"id": "cold", "phone": "5583999", "active": False}},
    ])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        result = await patients.get_contacts_for_patient("p1", role="agendamento")
    assert {c["phone"] for c in result} == {"5583111"}
