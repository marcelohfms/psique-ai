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


def test_self_messaging_new_minor_without_guardian_is_complete():
    # Menor NOVO que conversa em nome próprio (is_patient=True) — não há
    # responsável na conversa para exigir esses campos (caso Clara, 2026-07-21).
    u = _complete_minor(
        is_patient=True,
        is_returning_patient=False,
        guardian_name=None,
        guardian_relationship=None,
        guardian_cpf=None,
    )
    assert is_registration_complete(u) is True


def test_self_messaging_returning_minor_without_guardian_is_complete():
    # Mesmo caso, mas paciente já é da clínica.
    u = _complete_minor(
        is_patient=True,
        is_returning_patient=True,
        guardian_name=None,
        guardian_relationship=None,
        guardian_cpf=None,
    )
    assert is_registration_complete(u) is True


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
    for m in ("select", "eq", "in_", "limit", "maybe_single", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    table.not_ = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock,
               return_value=[{"id": "p-99", "patient_name": "Fulano"}]):
        await database.get_upcoming_appointments("5583999999999")
    # o filtro foi por patient_id, via in_ (cobre múltiplos pacientes do contato)
    table.in_.assert_any_call("patient_id", ["p-99"])
    # nunca filtrou por user_id
    assert all(c.args[0] != "user_id" for c in table.eq.call_args_list)


async def test_get_upcoming_appointments_covers_all_contact_patients():
    """Contato com vários pacientes: deve trazer consultas de TODOS, com patient_name
    anexado em cada linha (bug Silvia/Daniela — get_user_by_phone pegava paciente
    arbitrário e a Eva ficava cega para a consulta do outro paciente)."""
    users = [
        {"id": "p-silvia", "patient_name": "Silvia De Souza Passos"},
        {"id": "p-daniela", "patient_name": "Daniela De Souza Passos"},
    ]
    future_rows = [{
        "appointment_id": "a1",
        "start_time": "2026-07-20T20:00:00+00:00",
        "end_time": "2026-07-20T21:00:00+00:00",
        "status": "scheduled",
        "patient_id": "p-silvia",
    }]
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    table.not_ = table
    # 1ª execução = query de futuros; 2ª = recém-terminados; 3ª = concluídos com saldo
    # pendente; 4ª = pending_reschedule antigo; 5ª = cancelados por falta de pagamento
    table.execute = AsyncMock(side_effect=[
        MagicMock(data=future_rows), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]),
    ])
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock, return_value=users):
        result = await database.get_upcoming_appointments("5581981179458")
    # consultou por TODOS os patient_ids do contato
    table.in_.assert_any_call("patient_id", ["p-silvia", "p-daniela"])
    # anexou o nome do paciente correto na consulta retornada
    assert result and result[0]["patient_name"] == "Silvia De Souza Passos"


@pytest.mark.asyncio
async def test_get_upcoming_appointments_flags_completed_unpaid_as_already_occurred():
    """A completed appointment still owing a balance (paid_at IS NULL) must come back
    tagged already_occurred=True — regardless of how long ago it happened — so the
    LLM never talks about settling the balance "no dia da consulta" (caso Geórgia,
    2026-07-21: consulta já realizada, Eva tratou o saldo como se fosse futuro)."""
    users = [{"id": "p-georgia", "patient_name": "Geórgia"}]
    past_unpaid_rows = [{
        "appointment_id": "a-past",
        "start_time": "2026-06-01T12:00:00+00:00",
        "end_time": "2026-06-01T13:00:00+00:00",
        "status": "completed",
        "patient_id": "p-georgia",
        "paid_at": None,
        "booking_fee_paid_at": "2026-05-20T12:00:00+00:00",
        "booking_fee_waived": False,
    }]
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    table.not_ = table
    # 1ª = futuros (vazio); 2ª = recém-terminados (vazio); 3ª = concluídos com saldo
    # pendente; 4ª = pending_reschedule antigo (vazio); 5ª = cancelados por falta de
    # pagamento (vazio)
    table.execute = AsyncMock(side_effect=[
        MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=past_unpaid_rows), MagicMock(data=[]), MagicMock(data=[]),
    ])
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock, return_value=users):
        result = await database.get_upcoming_appointments("5583998264807")
    assert len(result) == 1
    assert result[0]["already_occurred"] is True
    assert result[0]["appointment_id"] == "a-past"


@pytest.mark.asyncio
async def test_get_upcoming_appointments_flags_stale_pending_reschedule():
    """Um pending_reschedule cujo end_time original já passou há mais de 48h deve
    aparecer no resultado com stale_reschedule=True — sem esse bucket, a linha some
    do prompt inteiro assim que passa da janela de 'recém-terminado', e a Eva perde
    todo sinal de que existe uma remarcação pendente (caso Heitor/Ludmilla,
    5581996937559, 21/07/2026: pending_reschedule de 02/07 ficou invisível até
    19/07, quando a Eva tratou a volta da paciente como agendamento novo)."""
    users = [{"id": "p-heitor", "patient_name": "Heitor"}]
    stale_rows = [{
        "appointment_id": "a-stale",
        "start_time": "2026-07-02T21:00:00+00:00",
        "end_time": "2026-07-02T23:00:00+00:00",
        "status": "pending_reschedule",
        "patient_id": "p-heitor",
        "booking_fee_paid_at": "2026-06-27T12:54:11+00:00",
        "booking_fee_waived": False,
    }]
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    table.not_ = table
    # 1ª = futuros (vazio); 2ª = recém-terminados (vazio); 3ª = concluídos com saldo
    # pendente (vazio); 4ª = pending_reschedule antigo (stale_rows); 5ª = cancelados
    # por falta de pagamento (vazio)
    table.execute = AsyncMock(side_effect=[
        MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=stale_rows), MagicMock(data=[]),
    ])
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock, return_value=users):
        result = await database.get_upcoming_appointments("5581996937559")
    assert len(result) == 1
    assert result[0]["stale_reschedule"] is True
    assert result[0]["appointment_id"] == "a-stale"


@pytest.mark.asyncio
async def test_get_upcoming_appointments_flags_recent_cancellation_for_nonpayment():
    """Uma consulta cancelada automaticamente por falta de pagamento da taxa de
    reserva (payment_reminder_sent_at preenchido, booking_fee_paid_at nulo), sem
    nenhum reagendamento posterior, deve voltar marcada com canceled_unpaid=True —
    sem isso a Eva não tem como saber que precisa checar disponibilidade antes de
    confirmar um novo agendamento quando o contato disser que quer remarcar (caso
    João Pedro Lins Da Costa Gomes, 5581992349207, 2026-07-30: a Eva confirmou
    verbalmente um reagendamento sem chamar nenhuma ferramenta)."""
    users = [{"id": "p-joao", "patient_name": "João Pedro"}]
    canceled_rows = [{
        "appointment_id": "a-canceled",
        "start_time": "2026-08-03T17:00:00+00:00",
        "end_time": "2026-08-03T18:00:00+00:00",
        "status": "canceled",
        "patient_id": "p-joao",
        "payment_reminder_sent_at": "2026-07-29T10:05:00+00:00",
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
        "doctor_id": DOCTOR_IDS["julio"],
    }]
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    table.not_ = table
    # 1ª=futuros(vazio) 2ª=recém-terminados(vazio) 3ª=saldo pendente(vazio)
    # 4ª=pending_reschedule antigo(vazio) 5ª=cancelados por falta de pagamento
    table.execute = AsyncMock(side_effect=[
        MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]),
        MagicMock(data=canceled_rows),
    ])
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock, return_value=users):
        result = await database.get_upcoming_appointments("5581992349207")
    assert len(result) == 1
    assert result[0]["canceled_unpaid"] is True
    assert result[0]["appointment_id"] == "a-canceled"


@pytest.mark.asyncio
async def test_get_upcoming_appointments_excludes_canceled_unpaid_when_already_rebooked():
    """Se o paciente já tem uma consulta ativa, uma cancelação antiga por falta de
    pagamento não deve aparecer também como canceled_unpaid — senão a Eva veria duas
    listagens conflitantes para o mesmo paciente."""
    users = [{"id": "p-joao", "patient_name": "João Pedro"}]
    future_rows = [{
        "appointment_id": "a-active",
        "start_time": "2026-08-10T14:00:00+00:00",
        "end_time": "2026-08-10T15:00:00+00:00",
        "status": "scheduled",
        "patient_id": "p-joao",
    }]
    canceled_rows = [{
        "appointment_id": "a-canceled",
        "start_time": "2026-08-03T17:00:00+00:00",
        "end_time": "2026-08-03T18:00:00+00:00",
        "status": "canceled",
        "patient_id": "p-joao",
        "payment_reminder_sent_at": "2026-07-29T10:05:00+00:00",
        "booking_fee_paid_at": None,
        "doctor_id": DOCTOR_IDS["julio"],
    }]
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "gte", "lt", "is_"):
        getattr(table, m).return_value = table
    table.not_ = table
    table.execute = AsyncMock(side_effect=[
        MagicMock(data=future_rows), MagicMock(data=[]), MagicMock(data=[]), MagicMock(data=[]),
        MagicMock(data=canceled_rows),
    ])
    client = MagicMock()
    client.from_.return_value = table
    with patch("app.database.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.database.get_users_by_phone", new_callable=AsyncMock, return_value=users):
        result = await database.get_upcoming_appointments("5581992349207")
    assert len(result) == 1
    assert result[0]["appointment_id"] == "a-active"


# ── missing_registration_field ───────────────────────────────────────────────
# Fonte única da completude do cadastro: o collect_info usa o nome do campo
# devolvido aqui para fazer uma pergunta determinística, de modo que a conversa
# nunca fique presa no collect_info sem ninguém perguntar pelo campo que falta
# (caso Bernardo Lima Beltrão Teixeira, 5581987415206, 31/07/2026).

def test_missing_registration_field_names_the_blocking_field():
    from app.database import missing_registration_field
    assert missing_registration_field(_complete_minor(guardian_relationship=None)) == "guardian_relationship"
    assert missing_registration_field(_complete_minor(email=None)) == "email"
    assert missing_registration_field(_complete_minor(guardian_name=None)) == "guardian_name"
    assert missing_registration_field(
        _complete_minor(is_returning_patient=False, guardian_cpf=None)
    ) == "guardian_cpf"


def test_missing_registration_field_returns_none_when_complete():
    from app.database import missing_registration_field
    assert missing_registration_field(_complete_minor()) is None


def test_missing_registration_field_agrees_with_is_registration_complete():
    """As duas funções não podem divergir — is_registration_complete é um wrapper."""
    from app.database import missing_registration_field
    for user in (
        _complete_minor(),
        _complete_minor(guardian_relationship=None),
        _complete_minor(name=None),
        _complete_minor(is_patient=None),
        {},
    ):
        assert is_registration_complete(user) is (missing_registration_field(user) is None)


# ── Reconciliação de paciente retornante (caso Maria José, 10/08/2026) ───────
# Contato NOVO (5581982131153) agendou para paciente que JÁ era da clínica
# (cadastrada em 23/06 sob o telefone da nora, 5581981139373). upsert_user só
# resolvia por telefone, então gravou is_returning_patient=True mas criou um
# paciente duplicado. Quando is_returning_patient=True, deve buscar por nome
# normalizado + data de nascimento e reusar o cadastro existente.

def _reconcile_mocks(captured, resolved_patient=None):
    """Patches comuns dos testes de reconciliação; devolve o dict de patches."""
    async def fake_upsert_contact(phone, data):
        return "c-novo"

    async def fake_upsert_patient(data, patient_id=None):
        captured["patient_data"] = data
        captured["patient_id"] = patient_id
        return patient_id or "p-criado-novo"

    async def fake_link(patient_id, contact_id, role, is_self=False, relationship=None):
        captured.setdefault("links", []).append({"patient_id": patient_id, "role": role})

    return {
        "contact": patch("app.database.get_contact_by_phone", new_callable=AsyncMock,
                         return_value={"id": "c-novo", "phone": "5581982131153"}),
        "upsert_contact": patch("app.database.upsert_contact", side_effect=fake_upsert_contact),
        "upsert_patient": patch("app.database.upsert_patient", side_effect=fake_upsert_patient),
        "link": patch("app.database.link_patient_contact", side_effect=fake_link),
        "resolve": patch("app.patients.resolve_active_patient", new_callable=AsyncMock,
                         return_value={"patient": resolved_patient}),
    }


@pytest.mark.asyncio
async def test_upsert_user_returning_vincula_paciente_existente_por_nome_e_nascimento():
    """Telefone novo não resolve ninguém + is_returning_patient=True + payload com
    nome e nascimento → reusa o paciente existente em vez de inserir um novo."""
    captured = {}
    mocks = _reconcile_mocks(captured, resolved_patient=None)
    existing = {"id": "p-real", "name": "Maria José Alves de Farias", "birth_date": "20/08/1956"}
    with mocks["contact"], mocks["upsert_contact"], mocks["upsert_patient"], \
         mocks["link"], mocks["resolve"], \
         patch("app.patients.find_patient_by_name_birth", new_callable=AsyncMock,
               return_value=existing) as mock_find, \
         patch("app.patients.merge_duplicate_patient", new_callable=AsyncMock) as mock_merge:
        pid = await database.upsert_user("5581982131153", {
            "patient_name": "Maria Jose Alves de Farias",
            "birth_date": "20/08/1956",
            "is_returning_patient": True,
            "is_patient": False,
            "guardian_name": "Cláudia Farias",
            "guardian_relationship": "nora",
        })

    assert pid == "p-real"
    # o update foi aplicado ao paciente existente (nada de INSERT de novo paciente)
    assert captured["patient_id"] == "p-real"
    # o contato novo foi vinculado ao paciente existente
    assert all(l["patient_id"] == "p-real" for l in captured["links"])
    # sem duplicado criado nesta conversa, não há o que mesclar
    mock_merge.assert_not_awaited()
    # a busca exigiu nome + data de nascimento (proteção contra homônimos)
    args, kwargs = mock_find.call_args
    assert "Maria Jose Alves de Farias" in args
    assert "20/08/1956" in args


@pytest.mark.asyncio
async def test_upsert_user_returning_mescla_duplicado_recem_criado():
    """Fluxo real do chat: o paciente duplicado já foi criado no passo do nome
    (antes de sabermos is_returning_patient). Quando a resposta 'já é paciente'
    chega, o duplicado deve ser mesclado no cadastro existente."""
    captured = {}
    mocks = _reconcile_mocks(captured)
    dup = {"id": "p-dup", "name": "Maria Jose Alves de Farias", "birth_date": "20/08/1956"}
    existing = {"id": "p-real", "name": "Maria José Alves de Farias", "birth_date": "20/08/1956"}
    with mocks["contact"], mocks["upsert_contact"], mocks["upsert_patient"], \
         mocks["link"], mocks["resolve"], \
         patch("app.patients.get_patient_by_id", new_callable=AsyncMock,
               return_value=dup), \
         patch("app.patients.find_patient_by_name_birth", new_callable=AsyncMock,
               return_value=existing), \
         patch("app.patients.merge_duplicate_patient", new_callable=AsyncMock,
               return_value=True) as mock_merge:
        pid = await database.upsert_user(
            "5581982131153", {"is_returning_patient": True}, user_id="p-dup",
        )

    assert pid == "p-real"
    mock_merge.assert_awaited_once_with("p-dup", "p-real")
    # o update deste turno foi aplicado ao paciente real, não ao duplicado
    assert captured["patient_id"] == "p-real"


@pytest.mark.asyncio
async def test_upsert_user_returning_sem_match_segue_fluxo_normal():
    captured = {}
    mocks = _reconcile_mocks(captured)
    with mocks["contact"], mocks["upsert_contact"], mocks["upsert_patient"], \
         mocks["link"], mocks["resolve"], \
         patch("app.patients.find_patient_by_name_birth", new_callable=AsyncMock,
               return_value=None), \
         patch("app.patients.merge_duplicate_patient", new_callable=AsyncMock) as mock_merge:
        pid = await database.upsert_user("5581982131153", {
            "patient_name": "Paciente Realmente Novo",
            "birth_date": "01/01/1990",
            "is_returning_patient": True,
            "is_patient": True,
        })

    assert pid == "p-criado-novo"
    assert captured["patient_id"] is None  # insert normal
    mock_merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_user_returning_nao_mescla_quando_dup_tem_consulta():
    """merge_duplicate_patient recusou (paciente atual tem consultas — pode ser
    legítimo): mantém o id atual e não reponta nada."""
    captured = {}
    mocks = _reconcile_mocks(captured)
    dup = {"id": "p-atual", "name": "Maria José", "birth_date": "20/08/1956"}
    existing = {"id": "p-real", "name": "Maria José", "birth_date": "20/08/1956"}
    with mocks["contact"], mocks["upsert_contact"], mocks["upsert_patient"], \
         mocks["link"], mocks["resolve"], \
         patch("app.patients.get_patient_by_id", new_callable=AsyncMock, return_value=dup), \
         patch("app.patients.find_patient_by_name_birth", new_callable=AsyncMock,
               return_value=existing), \
         patch("app.patients.merge_duplicate_patient", new_callable=AsyncMock,
               return_value=False):
        pid = await database.upsert_user(
            "5581982131153", {"is_returning_patient": True}, user_id="p-atual",
        )

    assert pid == "p-atual"
    assert captured["patient_id"] == "p-atual"


@pytest.mark.asyncio
async def test_upsert_user_nao_reconcilia_quando_nao_e_returning():
    """Paciente novo (is_returning_patient ausente ou False) nunca dispara a
    busca por nome+nascimento — cadastro novo segue criando paciente novo."""
    for payload in (
        {"patient_name": "Ana Nova", "birth_date": "01/01/2000", "is_patient": True},
        {"patient_name": "Ana Nova", "birth_date": "01/01/2000",
         "is_returning_patient": False, "is_patient": True},
    ):
        captured = {}
        mocks = _reconcile_mocks(captured)
        with mocks["contact"], mocks["upsert_contact"], mocks["upsert_patient"], \
             mocks["link"], mocks["resolve"], \
             patch("app.patients.find_patient_by_name_birth",
                   new_callable=AsyncMock) as mock_find:
            await database.upsert_user("5581982131153", dict(payload))
        mock_find.assert_not_awaited()


# ── Propagação de nome corrompido contato → paciente ─────────────────────────
# Quando o paciente ainda não tem nome próprio, o shim copia o nome do contato.
# Um valor ruim no contato virava dois prontuários errados de uma vez: foi o que
# aconteceu com 5581991812399, cujo nome (contato E paciente) virou o texto de um
# comprovante de pagamento.

@pytest.mark.asyncio
async def test_upsert_user_nao_copia_nome_invalido_do_contato_para_o_paciente():
    contact = {"id": "c1", "phone": "5583988887777", "active": True}
    captured = {}
    lixo = "[imagem]: COMPROVANTE DE PAGAMENTO: valor transferido R$ 600,00, PSIQUE."

    async def fake_upsert_contact(phone, data):
        captured["contact_data"] = data
        return "c1"

    async def fake_upsert_patient(data, patient_id=None):
        captured["patient_data"] = data
        return patient_id or "p-new"

    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.upsert_contact", side_effect=fake_upsert_contact), \
         patch("app.database.upsert_patient", side_effect=fake_upsert_patient), \
         patch("app.database.link_patient_contact", new_callable=AsyncMock), \
         patch("app.patients.resolve_active_patient", new_callable=AsyncMock,
               return_value={"patient": None}):
        await database.upsert_user("5583988887777", {"name": lixo, "email": "j@x.com"})

    assert captured.get("patient_data", {}).get("name") != lixo


@pytest.mark.asyncio
async def test_upsert_user_continua_copiando_nome_valido_do_contato():
    """A cópia é o comportamento normal para quem agenda para si — só o valor
    que não parece nome é barrado."""
    contact = {"id": "c1", "phone": "5583988887777", "active": True}
    captured = {}

    async def fake_upsert_contact(phone, data):
        return "c1"

    async def fake_upsert_patient(data, patient_id=None):
        captured["patient_data"] = data
        return patient_id or "p-new"

    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.database.upsert_contact", side_effect=fake_upsert_contact), \
         patch("app.database.upsert_patient", side_effect=fake_upsert_patient), \
         patch("app.database.link_patient_contact", new_callable=AsyncMock), \
         patch("app.patients.resolve_active_patient", new_callable=AsyncMock,
               return_value={"patient": None}):
        await database.upsert_user("5583988887777", {"name": "Ana Luiza", "email": "j@x.com"})

    assert captured["patient_data"]["name"] == "Ana Luiza"
