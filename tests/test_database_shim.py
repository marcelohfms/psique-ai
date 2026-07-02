import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app import database
from app.database import is_registration_complete, DOCTOR_IDS


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
async def test_shim_falls_back_to_financial_name_when_contact_name_missing():
    """Regression: contacts.name pode ficar nulo em cadastros feitos fora do fluxo
    de chat (import em lote/script), fazendo a Eva reperguntar o nome/relação a
    cada turno (caso Nara/Anselmo, 5581996571022, 2026-07-02). Quando o contato
    NÃO é o paciente, financial_name é garantidamente o nome do responsável
    (diferente do patient_name) e serve de fallback seguro."""
    contact = {"id": "c1", "phone": "5581996571022", "name": None,
               "cpf": "057.565.904-12", "active": True, "manual_hold": False}
    pc_rows = [
        {"patient_id": "p-anselmo", "is_self": False, "relationship": "responsável",
         "role": "agendamento",
         "patients": {"id": "p-anselmo", "name": "Anselmo de Oliveira Carvalho Neto",
                      "email": "narafreitas@gmail.com", "birth_date": "2018-10-28",
                      "age": 7, "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
                      "financial_name": "Nara Freitas Carvalho",
                      "financial_cpf": "057.565.904-12"}},
    ]
    client = _mock_client(pc_rows)
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client):
        rows = await database.get_users_by_phone("5581996571022")
    u = rows[0]
    assert u["name"] == "Nara Freitas Carvalho"
    assert u["guardian_name"] == "Nara Freitas Carvalho"


@pytest.mark.asyncio
async def test_shim_does_not_use_financial_name_when_contact_is_self():
    """Quando is_self=True, o contato JÁ é o paciente — financial_name não deve
    ser usado como fallback (evita confundir o nome do contato)."""
    contact = {"id": "c1", "phone": "5581988887777", "name": None,
               "cpf": "333", "active": True, "manual_hold": False}
    pc_rows = [
        {"patient_id": "p1", "is_self": True, "relationship": "self", "role": "agendamento",
         "patients": {"id": "p1", "name": "Ana Souza", "email": "ana@x.com",
                      "financial_name": "Ana Souza"}},
    ]
    client = _mock_client(pc_rows)
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client):
        rows = await database.get_users_by_phone("5581988887777")
    assert rows[0]["name"] is None


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
async def test_upsert_user_patient_only_field_does_not_wipe_contact_name():
    """Regression: updating a patient-only field (e.g. email, patient_name) with no
    contact fields in the payload must NOT overwrite contacts.name with NULL.

    Bug found 2026-07-01 (Adriana conversation, 5581981464986): request_registration_update
    called upsert_user(phone, {"email": new_value}) — since "email" isn't a contact
    field, contact_data ended up empty, and the old fallback `contact_data or
    {"name": data.get("name")}` sent {"name": None} to upsert_contact, nulling out the
    contact's name on every partial patient-field update.
    """
    contact = {"id": "c1", "phone": "5583988887777", "active": True}
    captured = {}

    async def fake_upsert_contact(phone, data):
        captured["contact_data"] = data
        return "c1"

    async def fake_upsert_patient(data, patient_id=None):
        captured["patient_data"] = data
        return patient_id or "p-new"

    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.upsert_contact", side_effect=fake_upsert_contact), \
         patch("app.database.upsert_patient", side_effect=fake_upsert_patient), \
         patch("app.database.link_patient_contact", new_callable=AsyncMock):
        await database.upsert_user(
            "5583988887777",
            {"email": "novo@x.com"},
            user_id="p1",
        )
    # No contact field was in the payload — contact_data must stay empty,
    # never {"name": None}.
    assert captured["contact_data"] == {}


def _complete_minor(**overrides) -> dict:
    """Base de um cadastro de MENOR (do Dr. Júlio) completo, estilo dict legado."""
    u = {
        "name": "Maria Silva",
        "email": "maria@x.com",
        "birth_date": "2016-03-10",
        "doctor_id": DOCTOR_IDS["julio"],
        "is_patient": False,
        "patient_name": "João Silva",
        "age": 10,
        "guardian_name": "Maria Silva",
        "guardian_relationship": "mãe",
        "guardian_cpf": "555",
        "is_returning_patient": True,
    }
    u.update(overrides)
    return u


def test_minor_returning_without_guardian_cpf_is_complete():
    # Paciente menor que JÁ é da clínica não precisa de guardian_cpf.
    u = _complete_minor(is_returning_patient=True, guardian_cpf=None)
    assert is_registration_complete(u) is True


def test_minor_new_without_guardian_cpf_is_incomplete():
    # Paciente menor NOVO ainda exige guardian_cpf (regressão preservada).
    u = _complete_minor(is_returning_patient=False, guardian_cpf=None)
    assert is_registration_complete(u) is False


def test_minor_returning_still_requires_guardian_name_and_relationship():
    assert is_registration_complete(_complete_minor(guardian_name=None)) is False
    assert is_registration_complete(_complete_minor(guardian_relationship=None)) is False


def test_julio_minor_undetermined_returning_status_is_incomplete():
    # Menor do Dr. Júlio sem is_returning_patient → incompleto (define preço/2 momentos).
    assert is_registration_complete(_complete_minor(is_returning_patient=None)) is False


def test_bruna_minor_undetermined_returning_status_is_complete():
    # Menor da Dra. Bruna sem is_returning_patient → completo (campo é irrelevante).
    u = _complete_minor(doctor_id=DOCTOR_IDS["bruna"], is_returning_patient=None)
    assert is_registration_complete(u) is True


def test_adult_returning_without_patient_cpf_is_complete():
    u = {
        "name": "Ana Souza", "email": "ana@x.com", "birth_date": "1990-08-22",
        "doctor_id": DOCTOR_IDS["bruna"], "is_patient": True, "age": 35,
        "is_returning_patient": True,
    }
    assert is_registration_complete(u) is True


def test_adult_undetermined_returning_status_is_complete():
    # Adulto sem is_returning_patient → completo (campo não é obrigatório).
    u = {
        "name": "Ana Souza", "email": "ana@x.com", "birth_date": "1990-08-22",
        "doctor_id": DOCTOR_IDS["julio"], "is_patient": True, "age": 35,
        "is_returning_patient": None,
    }
    assert is_registration_complete(u) is True


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
