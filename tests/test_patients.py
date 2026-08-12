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


def test_normalize_phone_passthrough_non_br_and_empty():
    # Números que não casam o padrão BR (55 + DDD + 8/9 dígitos) passam cru.
    assert patients.normalize_phone("") == ""
    assert patients.normalize_phone("12345") == "12345"
    assert patients.normalize_phone("447911123456") == "447911123456"  # UK, não-BR


def test_normalize_phone_preserves_international_numbers():
    """Números internacionais reais NÃO podem ganhar o 9 nem o 55 — passam intactos.

    Casos reais de pacientes da clínica (não inserir o 9 de celular brasileiro
    nem prefixar 55). O JID do WhatsApp já entrega o número com o código do país.
    """
    intl = {
        "351968021825": "351968021825",   # Portugal (+351)
        "34637036406": "34637036406",     # Espanha (+34)
        "12033646976": "12033646976",     # EUA (+1)
        "44759333090": "44759333090",     # Reino Unido (+44)
        "61411598693": "61411598693",     # Austrália (+61)
        "351968021825@s.whatsapp.net": "351968021825",  # JID é limpo
    }
    for raw, expected in intl.items():
        assert patients.normalize_phone(raw) == expected, raw


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


@pytest.mark.asyncio
async def test_get_contacts_for_patient_include_inactive_returns_all():
    # Lembretes de consulta são transacionais e devem chegar mesmo se o bot
    # estiver pausado para o contato (ex.: transferido para atendimento humano).
    client, table, execute = _client_returning([
        {"contact_id": "cpai", "contacts": {"id": "cpai", "phone": "5583111", "active": True}},
        {"contact_id": "cold", "contacts": {"id": "cold", "phone": "5583999", "active": False}},
    ])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        result = await patients.get_contacts_for_patient("p1", role="agendamento", include_inactive=True)
    assert {c["phone"] for c in result} == {"5583111", "5583999"}


@pytest.mark.asyncio
async def test_upsert_contact_inserts_when_absent():
    insert_exec = AsyncMock(return_value=MagicMock(data=[{"id": "c-new", "phone": "5583988887777"}]))
    select_exec = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("select", "eq", "insert", "update"):
        getattr(table, m).return_value = table
    table.execute = select_exec
    table.insert.return_value.execute = insert_exec
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        cid = await patients.upsert_contact("5583988887777", {"name": "João"})
    assert cid == "c-new"


@pytest.mark.asyncio
async def test_upsert_contact_updates_when_present():
    client, table, execute = _client_returning([{"id": "c1", "phone": "5583988887777"}])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        cid = await patients.upsert_contact("5583988887777", {"name": "João Silva"})
    assert cid == "c1"
    table.update.assert_called()


@pytest.mark.asyncio
async def test_upsert_patient_insert_returns_id():
    insert_exec = AsyncMock(return_value=MagicMock(data=[{"id": "p-new"}]))
    table = MagicMock()
    for m in ("select", "eq", "insert", "update"):
        getattr(table, m).return_value = table
    table.insert.return_value.execute = insert_exec
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        pid = await patients.upsert_patient({"name": "João"})
    assert pid == "p-new"


@pytest.mark.asyncio
async def test_upsert_patient_dedups_by_name_and_birth_date():
    """Mesmo nome + mesma data de nascimento = mesma pessoa; não cria duplicata."""
    select_exec = AsyncMock(return_value=MagicMock(
        data=[{"id": "p-existing", "name": "Jonas Santos Ferreira", "birth_date": "09/07/1987"}]
    ))
    update_exec = AsyncMock(return_value=MagicMock(data=[{"id": "p-existing"}]))
    table = MagicMock()
    for m in ("select", "eq", "in_", "insert"):
        getattr(table, m).return_value = table
    table.execute = select_exec
    table.update.return_value.eq.return_value.execute = update_exec
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        pid = await patients.upsert_patient({"name": "Jonas Santos Ferreira", "birth_date": "09/07/1987"})
    assert pid == "p-existing"
    table.insert.assert_not_called()
    table.update.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_patient_dedup_ignora_acentos_e_caixa():
    """A grafia do nome varia entre conversas ('José' vs 'Jose', caixa alta) —
    a mesma pessoa não pode virar dois prontuários por causa de acento."""
    select_exec = AsyncMock(return_value=MagicMock(
        data=[{"id": "p-existing", "name": "MARIA JOSÉ ALVES DE FARIAS", "birth_date": "20/08/1956"}]
    ))
    update_exec = AsyncMock(return_value=MagicMock(data=[{"id": "p-existing"}]))
    table = MagicMock()
    for m in ("select", "eq", "in_", "insert"):
        getattr(table, m).return_value = table
    table.execute = select_exec
    table.update.return_value.eq.return_value.execute = update_exec
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        pid = await patients.upsert_patient(
            {"name": "Maria Jose Alves de Farias", "birth_date": "20/08/1956"}
        )
    assert pid == "p-existing"
    table.insert.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_patient_inserts_when_no_name_birth_date_match():
    select_exec = AsyncMock(return_value=MagicMock(data=[]))
    insert_exec = AsyncMock(return_value=MagicMock(data=[{"id": "p-new"}]))
    table = MagicMock()
    for m in ("select", "eq", "in_"):
        getattr(table, m).return_value = table
    table.execute = select_exec
    table.insert.return_value.execute = insert_exec
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        pid = await patients.upsert_patient({"name": "Jonas Santos Ferreira", "birth_date": "09/07/1987"})
    assert pid == "p-new"
    table.insert.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_patient_update_path_returns_same_id():
    client, table, execute = _client_returning([])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        pid = await patients.upsert_patient({"name": "Novo Nome"}, patient_id="p-existing")
    assert pid == "p-existing"
    table.update.assert_called()


@pytest.mark.asyncio
async def test_link_patient_contact_upserts_on_conflict():
    client, table, execute = _client_returning([{"id": "pc1"}])
    table.upsert.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        await patients.link_patient_contact(
            "p1", "c1", "agendamento", is_self=True, relationship="mãe"
        )
    table.upsert.assert_called_once()
    args, kwargs = table.upsert.call_args
    assert args[0]["patient_id"] == "p1"
    assert args[0]["role"] == "agendamento"
    assert args[0]["is_self"] is True
    assert args[0]["relationship"] == "mãe"
    assert kwargs.get("on_conflict") == "patient_id,contact_id,role"


@pytest.mark.asyncio
async def test_link_patient_contact_relationship_defaults_to_none():
    client, table, execute = _client_returning([{"id": "pc1"}])
    table.upsert.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        await patients.link_patient_contact("p1", "c1", "agendamento")
    args, kwargs = table.upsert.call_args
    assert args[0]["relationship"] is None


@pytest.mark.asyncio
async def test_upsert_contact_persists_cpf():
    client, table, execute = _client_returning([{"id": "c1", "phone": "5583988887777"}])
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        cid = await patients.upsert_contact("5583988887777", {"name": "Maria", "cpf": "555"})
    assert cid == "c1"
    table.update.assert_called_once()
    args, _ = table.update.call_args
    assert args[0]["cpf"] == "555"


# ── Reconciliação de paciente retornante (caso Maria José, 5581982131153) ────
# Um contato NOVO agendando para um paciente que JÁ é da clínica não pode gerar
# um segundo prontuário: buscamos por nome normalizado + data de nascimento.

def test_normalize_person_name_ignora_acentos_caixa_e_espacos():
    assert patients.normalize_person_name("  Maria  JOSÉ Alves   de Farias ") == \
        "maria jose alves de farias"
    assert patients.normalize_person_name("João") == patients.normalize_person_name("JOAO")
    assert patients.normalize_person_name(None) == ""


def _client_multi_table(rows_by_table):
    """Client mockado que devolve linhas diferentes por tabela."""
    client = MagicMock()
    tables = {}

    def _from(name):
        if name not in tables:
            table = MagicMock()
            for m in ("select", "eq", "neq", "in_", "insert", "update", "delete",
                      "limit", "order", "upsert"):
                getattr(table, m).return_value = table
            table.execute = AsyncMock(return_value=MagicMock(data=rows_by_table.get(name, [])))
            tables[name] = table
        return tables[name]

    client.from_.side_effect = _from
    return client, tables


async def test_find_patient_by_name_birth_casa_sem_acentos_e_caixa():
    client, tables = _client_multi_table({"patients": [
        {"id": "p-existing", "name": "MARIA JOSÉ ALVES DE FARIAS", "birth_date": "20/08/1956"},
        {"id": "p-outro", "name": "Pedro Lima", "birth_date": "20/08/1956"},
    ]})
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        found = await patients.find_patient_by_name_birth(
            "maria jose alves de farias", "20/08/1956"
        )
    assert found["id"] == "p-existing"


async def test_find_patient_by_name_birth_consulta_ambos_formatos_de_data():
    """patients.birth_date convive com dd/mm/aaaa (fluxo do chat) e ISO (imports);
    a busca deve cobrir os dois formatos da mesma data."""
    client, tables = _client_multi_table({"patients": [
        {"id": "p-iso", "name": "Maria José Alves de Farias", "birth_date": "1956-08-20"},
    ]})
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        found = await patients.find_patient_by_name_birth(
            "Maria José Alves de Farias", "20/08/1956"
        )
    assert found["id"] == "p-iso"
    args, _ = tables["patients"].in_.call_args
    assert args[0] == "birth_date"
    assert set(args[1]) == {"20/08/1956", "1956-08-20"}


async def test_find_patient_by_name_birth_none_sem_match_ou_homonimos():
    # Nenhuma linha com a data → None.
    client, _ = _client_multi_table({"patients": []})
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        assert await patients.find_patient_by_name_birth("Maria José", "20/08/1956") is None

    # Dois homônimos com a MESMA data de nascimento → ambíguo, não escolhe.
    client, _ = _client_multi_table({"patients": [
        {"id": "p1", "name": "Maria José", "birth_date": "20/08/1956"},
        {"id": "p2", "name": "Maria José", "birth_date": "20/08/1956"},
    ]})
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        assert await patients.find_patient_by_name_birth("Maria José", "20/08/1956") is None


async def test_find_patient_by_name_birth_exclui_o_proprio_id():
    client, _ = _client_multi_table({"patients": [
        {"id": "p-dup", "name": "Maria José", "birth_date": "20/08/1956"},
        {"id": "p-real", "name": "Maria José", "birth_date": "20/08/1956"},
    ]})
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        found = await patients.find_patient_by_name_birth(
            "Maria José", "20/08/1956", exclude_id="p-dup"
        )
    assert found["id"] == "p-real"


async def test_merge_duplicate_patient_reponta_links_e_apaga_duplicado():
    client, tables = _client_multi_table({
        "appointments": [],  # duplicado recém-criado, sem consultas
        "patient_contacts": [
            {"patient_id": "p-dup", "contact_id": "c-nora", "role": "agendamento",
             "is_self": False, "relationship": "nora"},
        ],
        "patients": [],
    })
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.link_patient_contact", new_callable=AsyncMock) as mock_link:
        merged = await patients.merge_duplicate_patient("p-dup", "p-real")

    assert merged is True
    # link do contato foi recriado apontando para o paciente REAL
    mock_link.assert_awaited_once_with(
        "p-real", "c-nora", "agendamento", is_self=False, relationship="nora"
    )
    # links e linha do duplicado foram removidos
    tables["patient_contacts"].delete.assert_called_once()
    tables["patients"].delete.assert_called_once()


async def test_merge_duplicate_patient_aborta_se_dup_tem_consulta():
    """Guard anti-engano: se o 'duplicado' tem QUALQUER consulta, pode ser um
    paciente legítimo de longa data — não mesclar."""
    client, tables = _client_multi_table({
        "appointments": [{"id": "a1"}],
    })
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.link_patient_contact", new_callable=AsyncMock) as mock_link:
        merged = await patients.merge_duplicate_patient("p-dup", "p-real")

    assert merged is False
    mock_link.assert_not_awaited()
    # nada além da checagem de appointments foi consultado — nenhum delete
    assert "patients" not in tables and "patient_contacts" not in tables


async def test_merge_duplicate_patient_recusa_ids_iguais_ou_vazios():
    assert await patients.merge_duplicate_patient("p1", "p1") is False
    assert await patients.merge_duplicate_patient(None, "p1") is False
    assert await patients.merge_duplicate_patient("p1", None) is False


@pytest.mark.asyncio
async def test_resolve_active_patient_no_contact_returns_none():
    with patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=None):
        result = await patients.resolve_active_patient("5583988887777")
    assert result == {"contact": None, "patient": None, "candidates": [], "ambiguous": False}


@pytest.mark.asyncio
async def test_resolve_active_patient_single_patient():
    contact = {"id": "c1", "phone": "5583988887777"}
    with patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock,
               return_value=[{"id": "p1", "name": "João"}]):
        result = await patients.resolve_active_patient("5583988887777")
    assert result["contact"] == contact
    assert result["patient"]["id"] == "p1"
    assert result["ambiguous"] is False


@pytest.mark.asyncio
async def test_resolve_active_patient_multi_picks_upcoming():
    contact = {"id": "c1"}
    cands = [{"id": "p1", "name": "João"}, {"id": "p2", "name": "Maria"}]
    async def fake_has_upcoming(pid):
        return pid == "p2"
    with patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=cands), \
         patch("app.patients._patient_has_upcoming_appointment", side_effect=fake_has_upcoming):
        result = await patients.resolve_active_patient("5583988887777")
    assert result["patient"]["id"] == "p2"
    assert result["ambiguous"] is False


@pytest.mark.asyncio
async def test_resolve_active_patient_multi_ambiguous_when_none_upcoming():
    contact = {"id": "c1"}
    cands = [{"id": "p1"}, {"id": "p2"}]
    with patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=cands), \
         patch("app.patients._patient_has_upcoming_appointment",
               new_callable=AsyncMock, return_value=False):
        result = await patients.resolve_active_patient("5583988887777")
    assert result["patient"] is None
    assert result["ambiguous"] is True
    assert result["candidates"] == cands


@pytest.mark.asyncio
async def test_resolve_active_patient_ambiguous_when_multiple_upcoming():
    contact = {"id": "c1"}
    cands = [{"id": "p1"}, {"id": "p2"}]
    # ambos têm agendamento próximo -> ainda ambíguo (não dá pra decidir)
    with patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=cands), \
         patch("app.patients._patient_has_upcoming_appointment", new_callable=AsyncMock, return_value=True):
        result = await patients.resolve_active_patient("5583988887777")
    assert result["patient"] is None
    assert result["ambiguous"] is True


# --- Task 1: _compute_age ---
from datetime import date
from unittest.mock import patch
from app.patients import _compute_age


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 12)


def test_compute_age_ddmmyyyy():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("15/01/1990") == 36


def test_compute_age_iso():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("1990-01-15") == 36


def test_compute_age_exactly_18_on_birthday():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("12/08/2008") == 18


def test_compute_age_day_before_18th_birthday():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("13/08/2008") == 17


def test_compute_age_none_and_garbage():
    assert _compute_age(None) is None
    assert _compute_age("") is None
    assert _compute_age("não sei") is None
