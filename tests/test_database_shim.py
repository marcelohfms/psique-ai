import pytest
from unittest.mock import AsyncMock, patch
from app import database


@pytest.mark.asyncio
async def test_get_users_by_phone_merges_contact_and_patients():
    contact = {"id": "c1", "phone": "5583988887777", "active": True, "manual_hold": False}
    pats = [{"id": "p1", "name": "João", "email": "j@x.com"},
            {"id": "p2", "name": "Maria", "email": "m@x.com"}]
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_patients_by_contact", new_callable=AsyncMock, return_value=pats):
        rows = await database.get_users_by_phone("5583988887777")
    assert {r["id"] for r in rows} == {"p1", "p2"}
    assert all(r["number"] == "5583988887777" for r in rows)
    assert all(r["active"] is True for r in rows)


@pytest.mark.asyncio
async def test_get_user_by_phone_returns_none_when_unknown():
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=None):
        assert await database.get_user_by_phone("5583988887777") is None


@pytest.mark.asyncio
async def test_upsert_user_routes_fields_to_patient_and_contact():
    contact = {"id": "c1", "phone": "5583988887777", "active": True}
    captured = {}

    async def fake_upsert_contact(phone, data):
        captured["contact_data"] = data
        return "c1"

    async def fake_upsert_patient(data, patient_id=None):
        captured["patient_data"] = data
        captured["patient_id"] = patient_id
        return patient_id or "p-new"

    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.upsert_contact", side_effect=fake_upsert_contact), \
         patch("app.database.upsert_patient", side_effect=fake_upsert_patient), \
         patch("app.database.link_patient_contact", new_callable=AsyncMock):
        pid = await database.upsert_user(
            "5583988887777",
            {"name": "João", "email": "j@x.com", "active": False, "doctor_id": "d1"},
            user_id="p1",
        )
    assert pid == "p1"
    assert captured["contact_data"].get("active") is False
    assert captured["patient_data"].get("email") == "j@x.com"
    assert "active" not in captured["patient_data"]


@pytest.mark.asyncio
async def test_upsert_user_routes_guardian_to_contact():
    contact = {"id": "c1", "phone": "5583988887777", "active": True}
    captured = {}

    async def fake_upsert_contact(phone, data):
        captured["contact_data"] = data
        return "c1"

    async def fake_upsert_patient(data, patient_id=None):
        captured["patient_data"] = data
        return patient_id or "p-new"

    async def fake_link(patient_id, contact_id, role, is_self=False, relationship=None):
        captured.setdefault("links", []).append(
            {"role": role, "is_self": is_self, "relationship": relationship}
        )

    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.upsert_contact", side_effect=fake_upsert_contact), \
         patch("app.database.upsert_patient", side_effect=fake_upsert_patient), \
         patch("app.database.link_patient_contact", side_effect=fake_link):
        await database.upsert_user(
            "5583988887777",
            {
                "patient_name": "João",
                "guardian_name": "Maria",
                "guardian_cpf": "555",
                "guardian_relationship": "mãe",
                "is_patient": False,
            },
            user_id="p1",
        )
    # (a) contact_data recebeu o cpf do responsável
    assert captured["contact_data"].get("cpf") == "555"
    # (b) patient_data não contém campos de guardião
    assert "guardian_cpf" not in captured["patient_data"]
    assert "guardian_name" not in captured["patient_data"]
    assert "guardian_relationship" not in captured["patient_data"]
    # (c) link_patient_contact chamado com relationship="mãe" e is_self=False
    assert all(link["relationship"] == "mãe" for link in captured["links"])
    assert all(link["is_self"] is False for link in captured["links"])
