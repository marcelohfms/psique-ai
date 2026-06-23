import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app import database
from app.database import is_registration_complete


def _mock_client(pc_rows):
    """Monta um client mockado cuja query de patient_contacts retorna pc_rows."""
    execute = AsyncMock(return_value=MagicMock(data=pc_rows))
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "maybe_single", "order"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client


@pytest.mark.asyncio
async def test_get_users_by_phone_merges_contact_and_patients():
    # Atualizado para o novo formato legado fiel: o shim agora consulta
    # patient_contacts (com is_self/relationship) em vez de receber patients crus.
    # Intenção preservada: merge de contato + múltiplos pacientes, id=patient_id,
    # number=phone, campos de contato presentes.
    contact = {"id": "c1", "phone": "5583988887777", "name": "Ana",
               "active": True, "manual_hold": False}
    pc_rows = [
        {"patient_id": "p1", "is_self": True, "relationship": "self", "role": "agendamento",
         "patients": {"id": "p1", "name": "João", "email": "j@x.com"}},
        {"patient_id": "p2", "is_self": True, "relationship": "self", "role": "agendamento",
         "patients": {"id": "p2", "name": "Maria", "email": "m@x.com"}},
    ]
    client = _mock_client(pc_rows)
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client):
        rows = await database.get_users_by_phone("5583988887777")
    assert {r["id"] for r in rows} == {"p1", "p2"}
    assert all(r["number"] == "5583988887777" for r in rows)
    assert all(r["active"] is True for r in rows)
    assert {r["patient_name"] for r in rows} == {"João", "Maria"}


@pytest.mark.asyncio
async def test_shim_read_adult_self_is_registration_complete():
    contact = {"id": "c1", "phone": "5581988887777", "name": "Ana Souza",
               "cpf": "333", "active": True, "manual_hold": False}
    pc_rows = [
        {"patient_id": "p1", "is_self": True, "relationship": "self", "role": "agendamento",
         "patients": {"id": "p1", "name": "Ana Souza", "email": "ana@x.com",
                      "birth_date": "1990-08-22", "age": 35, "doctor_id": "dra-bruna",
                      "is_returning_patient": True, "patient_cpf": "333"}},
    ]
    client = _mock_client(pc_rows)
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client):
        rows = await database.get_users_by_phone("5581988887777")
    assert len(rows) == 1
    u = rows[0]
    assert u["id"] == "p1"
    assert u["number"] == "5581988887777"
    assert u["name"] == "Ana Souza"
    assert u["is_patient"] is True
    assert u["is_returning_patient"] is True
    assert is_registration_complete(u) is True


@pytest.mark.asyncio
async def test_shim_read_minor_with_guardian_is_registration_complete():
    contact = {"id": "c-maria", "phone": "5581999990001", "name": "Maria Silva",
               "cpf": "555", "active": True, "manual_hold": False}
    pc_rows = [
        {"patient_id": "p-joao", "is_self": False, "relationship": "mãe", "role": "agendamento",
         "patients": {"id": "p-joao", "name": "João Silva", "email": "joao@x.com",
                      "birth_date": "2016-03-10", "age": 10, "doctor_id": "dr-julio",
                      "is_returning_patient": True, "patient_cpf": "111"}},
    ]
    client = _mock_client(pc_rows)
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client):
        rows = await database.get_users_by_phone("5581999990001")
    u = rows[0]
    assert u["name"] == "Maria Silva"
    assert u["patient_name"] == "João Silva"
    assert u["is_patient"] is False
    assert u["guardian_name"] == "Maria Silva"
    assert u["guardian_cpf"] == "555"
    assert u["guardian_relationship"] == "mãe"
    assert is_registration_complete(u) is True


@pytest.mark.asyncio
async def test_shim_read_dedups_patient_across_roles():
    contact = {"id": "c1", "phone": "5581988887777", "name": "Ana",
               "cpf": "333", "active": True, "manual_hold": False}
    pc_rows = [
        {"patient_id": "p1", "is_self": True, "relationship": "self", "role": r,
         "patients": {"id": "p1", "name": "Ana", "email": "a@x.com"}}
        for r in ("agendamento", "financeiro", "consulta")
    ]
    client = _mock_client(pc_rows)
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client):
        rows = await database.get_users_by_phone("5581988887777")
    assert len(rows) == 1
    assert rows[0]["id"] == "p1"


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


@pytest.mark.asyncio
async def test_get_upcoming_appointments_filters_by_patient_id():
    """get_upcoming_appointments deve filtrar appointments por patient_id (não user_id)."""
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "maybe_single", "order", "gte", "lt"):
        getattr(table, m).return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"id": "p-99"}):
        await database.get_upcoming_appointments("5583999999999")
    # o filtro foi por patient_id == id do paciente
    table.eq.assert_any_call("patient_id", "p-99")
    # nunca filtrou por user_id
    assert all(c.args[0] != "user_id" for c in table.eq.call_args_list)
