from datetime import date, datetime, timedelta

import pytest

import attendant_db

JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"


# ── Variantes de telefone ─────────────────────────────────────────────────────


def test_strip_phone_removes_suffix():
    assert attendant_db._strip_phone("5581999998888@s.whatsapp.net") == "5581999998888"


def test_phone_variants_13_digits():
    assert attendant_db._phone_variants("5581999998888") == ["5581999998888", "558199998888"]


def test_phone_variants_12_digits():
    assert attendant_db._phone_variants("558199998888") == ["5581999998888", "558199998888"]


def test_phone_variants_strips_suffix_first():
    assert attendant_db._phone_variants("5581999998888@s.whatsapp.net") == [
        "5581999998888",
        "558199998888",
    ]


# ── Resolução ─────────────────────────────────────────────────────────────────


@pytest.fixture
def patched_client(monkeypatch, fake_client):
    async def _get():
        return fake_client
    monkeypatch.setattr(attendant_db, "get_client", _get)
    return fake_client


async def test_resolve_finds_contact_and_patients(patched_client):
    patched_client.store["contacts"] = [
        {"id": "c1", "phone": "5581999998888", "name": "Maria"},
    ]
    patched_client.store["patient_contacts"] = [
        {"contact_id": "c1", "patient_id": "p1", "role": "agendamento", "is_self": False,
         "patients": {"id": "p1", "name": "João"}},
        {"contact_id": "c1", "patient_id": "p1", "role": "financeiro", "is_self": False,
         "patients": {"id": "p1", "name": "João"}},
    ]
    out = await attendant_db.resolve_contact_and_patients("5581999998888@s.whatsapp.net")
    assert out["contact"]["id"] == "c1"
    assert [p["id"] for p in out["patients"]] == ["p1"]  # dedup por id


async def test_resolve_uses_variant_without_9(patched_client):
    patched_client.store["contacts"] = [
        {"id": "c2", "phone": "558199998888", "name": "Ana"},
    ]
    out = await attendant_db.resolve_contact_and_patients("5581999998888")
    assert out["contact"]["id"] == "c2"
    assert out["patients"] == []


async def test_resolve_no_contact(patched_client):
    out = await attendant_db.resolve_contact_and_patients("5581900000000")
    assert out == {"contact": None, "patients": []}


# ── Leitura paciente + vínculo ────────────────────────────────────────────────


async def test_get_patient(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "João", "email": "j@x.com"}]
    out = await attendant_db.get_patient("p1")
    assert out["email"] == "j@x.com"


async def test_get_patient_missing(patched_client):
    assert await attendant_db.get_patient("nope") is None


async def test_get_link(patched_client):
    patched_client.store["patient_contacts"] = [
        {"id": "pc1", "patient_id": "p1", "contact_id": "c1", "role": "agendamento",
         "is_self": False, "relationship": "mãe"},
    ]
    out = await attendant_db.get_link("p1", "c1")
    assert out["id"] == "pc1"
    assert out["relationship"] == "mãe"


async def test_get_link_prefers_agendamento_row(patched_client):
    patched_client.store["patient_contacts"] = [
        {"id": "pc-cons", "patient_id": "p1", "contact_id": "c1", "role": "consulta",
         "is_self": True, "relationship": "mãe"},
        {"id": "pc-agen", "patient_id": "p1", "contact_id": "c1", "role": "agendamento",
         "is_self": False, "relationship": "mãe"},
    ]
    link = await attendant_db.get_link("p1", "c1")
    assert link["role"] == "agendamento"


# ── Leitura da data de retorno ────────────────────────────────────────────────


async def test_get_return_reminder_found(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "doctor_id": "d1",
         "return_interval": "2_meses", "next_return_date": "2026-09-15"},
    ]
    out = await attendant_db.get_return_reminder("p1")
    assert out["return_interval"] == "2_meses"
    assert out["next_return_date"] == "2026-09-15"


async def test_get_return_reminder_missing(patched_client):
    assert await attendant_db.get_return_reminder("nope") is None


# ── Updates com whitelist ─────────────────────────────────────────────────────


async def test_update_patient_only_whitelisted(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "João", "secret": "x"}]
    await attendant_db.update_patient("p1", {"name": "João Silva", "secret": "HACK", "age": 30})
    row = patched_client.store["patients"][0]
    assert row["name"] == "João Silva"   # permitido
    assert row["secret"] == "x"          # ignorado (fora da whitelist)
    assert row["age"] == 30              # permitido


async def test_update_patient_allows_social_name(patched_client):
    """Verify that social_name is whitelisted for patient updates."""
    patched_client.store["patients"] = [{
        "id": "p1",
        "name": "João da Silva",
        "social_name": None
    }]
    await attendant_db.update_patient("p1", {"social_name": "Jojo"})
    row = patched_client.store["patients"][0]
    assert row["social_name"] == "Jojo"


async def test_update_contact_whitelist(patched_client):
    patched_client.store["contacts"] = [{"id": "c1", "phone": "5581999998888", "name": "A"}]
    await attendant_db.update_contact("c1", {"name": "B", "manual_hold": True, "id": "EVIL"})
    row = patched_client.store["contacts"][0]
    assert row["name"] == "B"
    assert row["manual_hold"] is True
    assert row["id"] == "c1"             # id nunca é sobrescrito


async def test_update_link_whitelist(patched_client):
    """`role` não é mais editável pelo painel (Task 11 review): a UI só edita
    is_self/relationship, então a whitelist deve ignorar "role" e "patient_id"
    mesmo quando vêm no payload."""
    patched_client.store["patient_contacts"] = [
        {"id": "pc1", "patient_id": "p1", "contact_id": "c1", "role": "agendamento",
         "is_self": False, "relationship": None},
    ]
    await attendant_db.update_link("pc1", {"role": "consulta", "relationship": "pai", "patient_id": "X"})
    row = patched_client.store["patient_contacts"][0]
    assert row["role"] == "agendamento"  # ignorado pela whitelist
    assert row["relationship"] == "pai"
    assert "patient_id" not in row or row.get("patient_id") != "X"


async def test_update_link_propagates_marker_to_all_roles_of_pair(patched_client):
    """is_self/relationship são propriedade do PAR (paciente, contato): devem ser
    gravados em TODAS as roles do par, senão o cron de lembrete lê uma linha e o
    painel mostra outra."""
    patched_client.store["patient_contacts"] = [
        {"id": "pc-cons", "patient_id": "p1", "contact_id": "c1", "role": "consulta",
         "is_self": True, "relationship": None},
        {"id": "pc-agen", "patient_id": "p1", "contact_id": "c1", "role": "agendamento",
         "is_self": True, "relationship": None},
        {"id": "pc-fin", "patient_id": "p1", "contact_id": "c1", "role": "financeiro",
         "is_self": True, "relationship": None},
        # outro paciente com o mesmo contato: não deve ser afetado
        {"id": "pc-outro", "patient_id": "p2", "contact_id": "c1", "role": "agendamento",
         "is_self": True, "relationship": None},
    ]
    await attendant_db.update_link("pc-agen", {"is_self": False, "relationship": "mãe"})
    rows = {r["id"]: r for r in patched_client.store["patient_contacts"]}
    for pc_id in ("pc-cons", "pc-agen", "pc-fin"):
        assert rows[pc_id]["is_self"] is False
        assert rows[pc_id]["relationship"] == "mãe"
    # não vazou para outro paciente
    assert rows["pc-outro"]["is_self"] is True
    assert rows["pc-outro"]["relationship"] is None


# ── Escrita da data de retorno ────────────────────────────────────────────────


async def test_update_return_reminder_sets_date_and_resets_flags(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "return_interval": "2_meses",
         "next_return_date": "2026-09-15",
         "month_before_sent_at": "2026-08-01T00:00:00-03:00",
         "month_of_sent_at": None, "overdue_sent_at": None},
    ]
    updated = await attendant_db.update_return_reminder("p1", {"next_return_date": "2026-10-15"})
    assert updated is True
    row = patched_client.store["return_reminders"][0]
    assert row["next_return_date"] == "2026-10-15"
    assert row["month_before_sent_at"] is None
    assert row["month_of_sent_at"] is None
    assert row["overdue_sent_at"] is None
    assert row["updated_at"]  # timestamp preenchido


async def test_update_return_reminder_no_row_returns_false(patched_client):
    patched_client.store["return_reminders"] = []
    updated = await attendant_db.update_return_reminder("p1", {"next_return_date": "2026-10-15"})
    assert updated is False
    assert patched_client.store["return_reminders"] == []  # não cria linha


async def test_update_return_reminder_empty_data_noop(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "next_return_date": "2026-09-15"},
    ]
    updated = await attendant_db.update_return_reminder("p1", {"foo": "bar"})
    assert updated is False
    assert patched_client.store["return_reminders"][0]["next_return_date"] == "2026-09-15"


async def test_update_return_reminder_whitelist(patched_client):
    patched_client.store["return_reminders"] = [
        {"id": "r1", "patient_id": "p1", "next_return_date": "2026-09-15", "doctor_id": "d1"},
    ]
    updated = await attendant_db.update_return_reminder(
        "p1", {"next_return_date": "2026-10-15", "doctor_id": "hacker"}
    )
    assert updated is True
    row = patched_client.store["return_reminders"][0]
    assert row["next_return_date"] == "2026-10-15"
    assert row["doctor_id"] == "d1"  # fora da whitelist, não alterado


# ── Auditoria ─────────────────────────────────────────────────────────────────


async def test_log_event_inserts(patched_client):
    await attendant_db.log_event("attendant_edit_patient", "5581999998888@s.whatsapp.net",
                                 {"patient_id": "p1"})
    rows = patched_client.store["events"]
    assert len(rows) == 1
    assert rows[0]["event_type"] == "attendant_edit_patient"
    assert rows[0]["phone"] == "5581999998888"   # sem sufixo
    assert rows[0]["metadata"] == {"patient_id": "p1"}


async def test_log_event_swallows_errors(monkeypatch):
    async def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(attendant_db, "get_client", _boom)
    # não deve levantar
    await attendant_db.log_event("x", "5581999998888", None)


# ── Reset do checkpoint ───────────────────────────────────────────────────────


async def test_reset_checkpoint_deletes_all_tables_and_variants(patched_client):
    tid9 = "5581999998888@s.whatsapp.net"
    for t in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
        patched_client.store[t] = [
            {"thread_id": tid9, "x": 1},
            {"thread_id": "outro@s.whatsapp.net", "x": 2},
        ]
    deleted = await attendant_db.reset_checkpoint("5581999998888")
    assert deleted == 3  # uma linha por tabela
    for t in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
        remaining = [r["thread_id"] for r in patched_client.store[t]]
        assert remaining == ["outro@s.whatsapp.net"]


async def test_reset_checkpoint_matches_variant_without_9(patched_client):
    tid12 = "558199998888@s.whatsapp.net"  # gravado SEM o 9
    patched_client.store["checkpoints"] = [{"thread_id": tid12, "x": 1}]
    patched_client.store["checkpoint_writes"] = []
    patched_client.store["checkpoint_blobs"] = []
    deleted = await attendant_db.reset_checkpoint("5581999998888@s.whatsapp.net")
    assert deleted == 1
    assert patched_client.store["checkpoints"] == []


# ── Liga/desliga Eva por paciente (todos os contatos) ─────────────────────────


async def test_set_patient_eva_off_liga_em_todos_os_contatos(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "role": "agendamento"},
        {"patient_id": "p1", "contact_id": "c2", "role": "financeiro"},
    ]
    patched_client.store["contacts"] = [
        {"id": "c1", "manual_hold": False},
        {"id": "c2", "manual_hold": False},
    ]
    n = await attendant_db.set_patient_eva_off("p1", True)
    assert n == 2
    assert all(c["manual_hold"] for c in patched_client.store["contacts"])


async def test_set_patient_eva_off_religa(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "role": "agendamento"},
    ]
    patched_client.store["contacts"] = [{"id": "c1", "manual_hold": True}]
    await attendant_db.set_patient_eva_off("p1", False)
    assert patched_client.store["contacts"][0]["manual_hold"] is False


async def test_is_patient_eva_off_true_quando_algum_em_hold(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "contacts": {"manual_hold": False}},
        {"patient_id": "p1", "contact_id": "c2", "contacts": {"manual_hold": True}},
    ]
    assert await attendant_db.is_patient_eva_off("p1") is True


async def test_is_patient_eva_off_false_quando_nenhum(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "contacts": {"manual_hold": False}},
    ]
    assert await attendant_db.is_patient_eva_off("p1") is False


# ── Banco falso: ilike ────────────────────────────────────────────────────────


async def test_fake_ilike_casa_curinga_e_ignora_caixa(fake_client):
    fake_client.store["patients"] = [
        {"id": "p1", "name": "João Menezes"},
        {"id": "p2", "name": "Maria Souza"},
    ]
    res = await fake_client.from_("patients").select("id").ilike("name", "%j__o m%").execute()
    assert [r["id"] for r in res.data] == ["p1"]


# ── Marcador do vínculo ───────────────────────────────────────────────────────


def test_marker_proprio_zera_parentesco():
    assert attendant_db.normalize_marker(True, "mãe") == {"is_self": True, "relationship": None}


def test_marker_terceiro_exige_parentesco_da_lista():
    assert attendant_db.normalize_marker(False, "  mãe ") == {"is_self": False, "relationship": "mãe"}
    with pytest.raises(ValueError):
        attendant_db.normalize_marker(False, "")
    with pytest.raises(ValueError):
        attendant_db.normalize_marker(False, "vizinha")
    with pytest.raises(ValueError):
        attendant_db.normalize_marker(None, None)


def test_lista_de_parentesco_tem_acompanhante_e_nao_tem_outro():
    assert "acompanhante" in attendant_db.RELATIONSHIPS
    assert "outro" not in attendant_db.RELATIONSHIPS
    assert attendant_db.RELATIONSHIPS[0] == "mãe"


def test_marker_texto_false_nao_vira_proprio():
    assert attendant_db.normalize_marker("false", "mãe") == {"is_self": False, "relationship": "mãe"}
    with pytest.raises(ValueError):
        attendant_db.normalize_marker("true", None)


async def test_resolve_traz_vinculo_de_cada_paciente_preferindo_agendamento(patched_client):
    patched_client.store["contacts"] = [{"id": "c1", "phone": "5581999998888"}]
    patched_client.store["patient_contacts"] = [
        {"id": "pc-fin", "contact_id": "c1", "patient_id": "p1", "role": "financeiro",
         "is_self": False, "relationship": "pai", "patients": {"id": "p1", "name": "João"}},
        {"id": "pc-ag", "contact_id": "c1", "patient_id": "p1", "role": "agendamento",
         "is_self": False, "relationship": "mãe", "patients": {"id": "p1", "name": "João"}},
        {"id": "pc-2", "contact_id": "c1", "patient_id": "p2", "role": "agendamento",
         "is_self": True, "relationship": None, "patients": {"id": "p2", "name": "Carla"}},
    ]
    out = await attendant_db.resolve_contact_and_patients("5581999998888")
    by_id = {p["id"]: p for p in out["patients"]}
    assert by_id["p1"]["link"] == {"id": "pc-ag", "is_self": False, "relationship": "mãe"}
    assert by_id["p2"]["link"] == {"id": "pc-2", "is_self": True, "relationship": None}
    assert [p["id"] for p in out["patients"]] == ["p1", "p2"]


# ── Busca de paciente ─────────────────────────────────────────────────────────


async def test_busca_ignora_acento_e_caixa(patched_client):
    patched_client.store["patients"] = [
        {"id": "p1", "name": "João Menezes", "birth_date": "06/05/2014"},
        {"id": "p2", "name": "Joana Lima", "birth_date": "01/01/1990"},
        {"id": "p3", "name": "Pedro Alves", "birth_date": "02/02/1980"},
    ]
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "is_self": True, "contacts": {"phone": "5581999995432"}},
        {"patient_id": "p1", "is_self": False, "contacts": {"phone": "5581911110000"}},
    ]
    out = await attendant_db.search_patients("JOAO men")
    assert out == [{"id": "p1", "name": "João Menezes", "birth_date": "06/05/2014",
                    "phone_hint": "5432"}]


async def test_busca_com_menos_de_3_letras_nao_consulta(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "Jo"}]
    assert await attendant_db.search_patients("jo") == []


async def test_busca_ordena_por_nome_e_limita(patched_client):
    patched_client.store["patients"] = [
        {"id": f"p{i}", "name": f"Ana {chr(90 - i)}", "birth_date": None} for i in range(15)
    ]
    out = await attendant_db.search_patients("ana", limit=3)
    assert [p["name"] for p in out] == ["Ana L", "Ana M", "Ana N"]
    assert all(p["phone_hint"] is None for p in out)


async def test_busca_pagina_alem_de_mil_candidatos(patched_client):
    patched_client.store["patients"] = [
        {"id": f"p{i}", "name": f"Ana X{i}", "birth_date": None} for i in range(1205)
    ] + [{"id": "alvo", "name": "Ana Luísa Prado", "birth_date": None}]
    out = await attendant_db.search_patients("ana luisa")
    assert [p["id"] for p in out] == ["alvo"]


async def test_busca_pagina_ate_pagina_vazia_mesmo_com_max_rows_menor(monkeypatch, patched_client):
    """Se o servidor Supabase tiver um `max_rows` menor que `_SEARCH_PAGE_SIZE`
    (ex: 500), o Postgrest devolve páginas menores do que o pedido — mas não
    vazias — antes do fim dos dados. A paginação não pode parar nesse caso;
    só quando uma página vem vazia (ver leftover A do Task 9)."""
    from tests.conftest import FakeQuery

    # Ids em ordem lexicográfica: "p0000".."p1204" ficam todos ANTES de "zzz_alvo"
    # (a query ordena por `id`), então o alvo só aparece na última página.
    patched_client.store["patients"] = [
        {"id": f"p{i:04d}", "name": f"Ana X{i}", "birth_date": None} for i in range(1205)
    ] + [{"id": "zzz_alvo", "name": "Ana Luísa Prado", "birth_date": None}]

    # O ILIKE de verdade já foi coberto no teste acima; aqui o que importa é a
    # paginação, então faz o pré-filtro do banco falso casar tudo (como se o
    # ILIKE real casasse com folga) e deixa o filtro exato em Python separar o
    # alvo — exatamente como o código de produção faz.
    monkeypatch.setattr(attendant_db, "_ilike_pattern", lambda query: "%")

    real_execute = FakeQuery.execute

    async def capped_execute(self):
        """Simula um servidor com max_rows=500: nunca devolve mais que isso
        por página, mesmo quando o range pedido cobre mais linhas."""
        result = await real_execute(self)
        if self._op == "select" and self._range is not None:
            result.data = (result.data or [])[:500]
        return result

    monkeypatch.setattr(FakeQuery, "execute", capped_execute)

    out = await attendant_db.search_patients("ana luisa")
    assert [p["id"] for p in out] == ["zzz_alvo"]


async def test_busca_para_apos_20_paginas_e_avisa(monkeypatch, patched_client, caplog):
    """Trava de segurança: um servidor que nunca devolve página vazia (bug,
    ou uma base gigante de verdade) não pode deixar a busca paginar para
    sempre. Para com 20 páginas e loga um warning."""
    from tests.conftest import FakeQuery, FakeResult

    monkeypatch.setattr(attendant_db, "_ilike_pattern", lambda query: "%")

    calls = {"n": 0}

    async def infinite_execute(self):
        if self._op != "select" or self._range is None:
            return FakeResult([])
        calls["n"] += 1
        start, _end = self._range
        # Página sempre cheia (_SEARCH_PAGE_SIZE), nunca vazia: sem a trava,
        # isso paginaria para sempre.
        return FakeResult([
            {"id": f"p{start + i}", "name": "Ana Nunca Acaba", "birth_date": None}
            for i in range(attendant_db._SEARCH_PAGE_SIZE)
        ])

    monkeypatch.setattr(FakeQuery, "execute", infinite_execute)

    with caplog.at_level("WARNING"):
        await attendant_db.search_patients("ana nunca")

    assert calls["n"] == 20
    assert any("busca" in r.message.lower() or "pagina" in r.message.lower()
               for r in caplog.records)


async def test_busca_casa_nome_com_espaco_duplo_no_banco(patched_client):
    patched_client.store["patients"] = [
        {"id": "p1", "name": "Maria  Souza", "birth_date": None},
    ]
    out = await attendant_db.search_patients("maria souza")
    assert [p["id"] for p in out] == ["p1"]


# ── Vincular ──────────────────────────────────────────────────────────────────


async def test_vincular_cria_os_tres_papeis(patched_client):
    await attendant_db.link_patient("p1", "c1", {"is_self": False, "relationship": "mãe"})
    rows = patched_client.store["patient_contacts"]
    assert sorted(r["role"] for r in rows) == ["agendamento", "consulta", "financeiro"]
    assert all(r["patient_id"] == "p1" and r["contact_id"] == "c1" for r in rows)
    assert all(r["is_self"] is False and r["relationship"] == "mãe" for r in rows)


async def test_vincular_completa_papeis_faltantes_e_alinha_marcador(patched_client):
    patched_client.store["patient_contacts"] = [
        {"id": "pc1", "patient_id": "p1", "contact_id": "c1", "role": "agendamento",
         "is_self": True, "relationship": "mãe"},
    ]
    await attendant_db.link_patient("p1", "c1", {"is_self": True, "relationship": None})
    rows = patched_client.store["patient_contacts"]
    assert len(rows) == 3
    assert all(r["is_self"] is True and r["relationship"] is None for r in rows)


async def test_vincular_duas_vezes_e_idempotente_via_upsert(patched_client):
    await attendant_db.link_patient("p1", "c1", {"is_self": False, "relationship": "mãe"})
    await attendant_db.link_patient("p1", "c1", {"is_self": True, "relationship": None})
    rows = patched_client.store["patient_contacts"]
    assert len(rows) == 3
    assert sorted(r["role"] for r in rows) == ["agendamento", "consulta", "financeiro"]
    assert all(r["is_self"] is True and r["relationship"] is None for r in rows)


# ── Ficha nova ────────────────────────────────────────────────────────────────


def _replace_year(d, year):
    """`date.replace(year=...)` quebra com ValueError quando `d` é 29/02 e o
    ano alvo não é bissexto. Os testes de idade abaixo sempre partem de "hoje"
    e voltam anos, então caem no 28/02 nesse caso raro em vez de estourar."""
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(month=2, day=28, year=year)


def test_nascimento_aceita_br_e_iso_e_devolve_br():
    assert attendant_db.normalize_birth_date("06/05/2014") == "06/05/2014"
    assert attendant_db.normalize_birth_date(" 2014-05-06 ") == "06/05/2014"
    for ruim in ("", "31/02/2014", "amanhã", "01/01/2999"):
        with pytest.raises(ValueError):
            attendant_db.normalize_birth_date(ruim)


def test_nascimento_recusa_idade_acima_de_120_anos():
    today = datetime.now(attendant_db._TZ).date()
    muito_velho = _replace_year(today, today.year - 121)
    with pytest.raises(ValueError):
        attendant_db.normalize_birth_date(muito_velho.strftime("%d/%m/%Y"))

    no_limite = _replace_year(today, today.year - 120)
    assert attendant_db.normalize_birth_date(no_limite.strftime("%d/%m/%Y")) == (
        no_limite.strftime("%d/%m/%Y")
    )


async def test_duplicada_por_nome_e_nascimento_nas_duas_grafias(patched_client):
    patched_client.store["patients"] = [
        {"id": "p1", "name": "João  Menezes", "birth_date": "2014-05-06"},
        {"id": "p2", "name": "João Menezes", "birth_date": "07/05/2014"},
    ]
    out = await attendant_db.find_patients_by_name_birth("joao menezes", "06/05/2014")
    assert [p["id"] for p in out] == ["p1"]


async def test_criar_ficha_grava_nome_limpo_e_devolve_id(patched_client):
    created = await attendant_db.create_patient("  Ana   Luz ", "01/02/2015")
    row = patched_client.store["patients"][0]
    assert row["name"] == "Ana Luz" and row["birth_date"] == "01/02/2015"
    assert created["id"] == row["id"] and len(created["id"]) == 36
    assert created["name"] == "Ana Luz" and created["birth_date"] == "01/02/2015"
    assert created["age"] == row["age"]
    assert isinstance(created["age"], int)


async def test_criar_ficha_calcula_idade_com_e_sem_aniversario_no_ano(patched_client):
    """A idade é calculada a partir do nascimento e da data de hoje em
    America/Recife — precisa considerar o aniversário do ano já passado ou não."""
    today = datetime.now(attendant_db._TZ).date()

    ja_fez_aniversario = _replace_year(today, today.year - 8)
    created = await attendant_db.create_patient("Já fez", ja_fez_aniversario.strftime("%d/%m/%Y"))
    assert created["age"] == 8

    ainda_nao_fez = (today + timedelta(days=1))
    ainda_nao_fez = _replace_year(ainda_nao_fez, ainda_nao_fez.year - 8)
    created2 = await attendant_db.create_patient("Ainda não", ainda_nao_fez.strftime("%d/%m/%Y"))
    assert created2["age"] == 7


async def test_criar_ficha_recusa_nome_vazio(patched_client):
    with pytest.raises(ValueError):
        await attendant_db.create_patient("   ", "01/02/2015")
    assert patched_client.store.get("patients", []) == []


# ── Desvincular ───────────────────────────────────────────────────────────────


def _pc(pid, cid, role="agendamento"):
    return {"patient_id": pid, "contact_id": cid, "role": role, "is_self": False, "relationship": "mãe"}


async def test_trava_ultimo_numero_com_consulta_futura(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c1", "consulta")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None and "02/10/2099" in msg and "14:00" in msg


async def test_trava_converte_horario_utc_para_recife(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c1", "consulta")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T17:00:00+00:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None and "02/10/2099" in msg and "14:00" in msg


async def test_trava_trata_horario_sem_fuso_como_utc(patched_client):
    """start_time sem tzinfo (naive) é tratado como UTC antes do astimezone —
    sem isso, astimezone() assume o fuso do SERVIDOR (não confiável), o que já
    causou o card do guard errar o horário em -3h (ver memória)."""
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c1", "consulta")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T17:00:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None and "02/10/2099" in msg and "14:00" in msg


async def test_trava_inclui_nome_do_paciente(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "Lucas Menezes"}]
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c1", "consulta")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None and "Lucas Menezes" in msg


async def test_trava_sem_ficha_de_paciente_usa_fallback_generico(patched_client):
    """Sem a linha em `patients` (não deveria acontecer em produção, mas o
    banco falso dos outros testes desta seção não a povoa), a mensagem não
    quebra e usa um texto genérico no lugar do nome."""
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c1", "consulta")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None and "deste paciente" in msg


async def test_trava_persiste_quando_outro_numero_e_so_financeiro(patched_client):
    """Um contato ligado só como `financeiro` (legado) não conta como "outro
    número" de verdade: sem alguém em agendamento/consulta, ainda ficaria sem
    quem receber lembrete e cobrança."""
    patched_client.store["patient_contacts"] = [
        _pc("p1", "c1"), _pc("p1", "c1", "consulta"),
        {"patient_id": "p1", "contact_id": "c2", "role": "financeiro",
         "is_self": False, "relationship": "pai"},
    ]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None


async def test_sem_trava_quando_ha_outro_numero(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c2")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    assert await attendant_db.unlink_blocker("p1", "c1") is None


async def test_sem_trava_quando_so_ha_consulta_passada_ou_cancelada(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2020-01-01T10:00:00-03:00"},
        {"patient_id": "p1", "status": "canceled", "start_time": "2099-01-01T10:00:00-03:00"},
    ]
    assert await attendant_db.unlink_blocker("p1", "c1") is None


async def test_desvincular_apaga_so_as_linhas_do_par(patched_client):
    patched_client.store["patient_contacts"] = [
        _pc("p1", "c1"), _pc("p1", "c1", "financeiro"), _pc("p1", "c2"), _pc("p2", "c1"),
    ]
    removed = await attendant_db.unlink_patient("p1", "c1")
    assert removed == 2
    assert sorted((r["patient_id"], r["contact_id"]) for r in patched_client.store["patient_contacts"]) == [
        ("p1", "c2"), ("p2", "c1"),
    ]


# ── Aviso de consulta futura ainda "no nome" deste número ────────────────────


async def test_future_bookings_by_contact_conta_consultas_ativas_futuras(patched_client):
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "contact_id": "c1", "status": "scheduled",
         "start_time": "2099-10-02T14:00:00-03:00"},
        {"patient_id": "p1", "contact_id": "c1", "status": "pending_reschedule",
         "start_time": "2099-11-02T14:00:00-03:00"},
    ]
    n = await attendant_db.future_bookings_by_contact("p1", "c1")
    assert n == 2


async def test_future_bookings_by_contact_ignora_outro_contato_status_e_passado(patched_client):
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "contact_id": "c2", "status": "scheduled",
         "start_time": "2099-10-02T14:00:00-03:00"},  # outro contato
        {"patient_id": "p1", "contact_id": "c1", "status": "canceled",
         "start_time": "2099-10-02T14:00:00-03:00"},  # status inativo
        {"patient_id": "p1", "contact_id": "c1", "status": "scheduled",
         "start_time": "2020-01-01T10:00:00-03:00"},  # já passou
    ]
    assert await attendant_db.future_bookings_by_contact("p1", "c1") == 0


async def test_future_bookings_by_contact_sem_consultas_e_zero(patched_client):
    assert await attendant_db.future_bookings_by_contact("p1", "c1") == 0


# ── Consultas futuras + etiqueta 1ª consulta ─────────────────────────────────


async def test_list_consultas_future_active_only(patched_client, fake_client):
    fake_client.store["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "status": "scheduled", "doctor_id": JULIO,
         "start_time": "2099-10-05T12:00:00+00:00", "end_time": "2099-10-05T13:00:00+00:00",
         "modality": "presencial", "consultation_type": "primeira_consulta",
         "session_note": "1ª consulta · parte 1 de 2"},
        {"appointment_id": "a0", "patient_id": "p1", "status": "scheduled", "doctor_id": JULIO,
         "start_time": "2000-01-01T12:00:00+00:00", "end_time": "2000-01-01T13:00:00+00:00"},
        {"appointment_id": "a2", "patient_id": "p1", "status": "cancelled", "doctor_id": JULIO,
         "start_time": "2099-11-05T12:00:00+00:00", "end_time": "2099-11-05T13:00:00+00:00"},
        {"appointment_id": "c1", "patient_id": "p1", "status": "completed", "doctor_id": JULIO,
         "start_time": "2020-01-01T12:00:00+00:00", "end_time": "2020-01-01T13:00:00+00:00"},
    ]
    out = await attendant_db.list_consultas("p1")
    assert [a["appointment_id"] for a in out["appointments"]] == ["a1"]
    a = out["appointments"][0]
    assert a["start_local"] == "2099-10-05T09:00" and a["minutes"] == 60
    assert a["doctor_key"] == "julio"
    assert out["pending_part2"] == "a1"
    assert out["pending_part2_modality"] == "presencial"
    assert out["has_completed"] is True


async def test_list_consultas_part2_booked_clears_pending(patched_client, fake_client):
    base = {"patient_id": "p1", "status": "scheduled", "doctor_id": JULIO}
    fake_client.store["appointments"] = [
        {**base, "appointment_id": "a1", "start_time": "2099-10-05T12:00:00+00:00",
         "end_time": "2099-10-05T13:00:00+00:00", "session_note": "1ª consulta · parte 1 de 2"},
        {**base, "appointment_id": "a2", "start_time": "2099-10-08T12:00:00+00:00",
         "end_time": "2099-10-08T13:00:00+00:00", "session_note": "1ª consulta · parte 2 de 2"},
    ]
    out = await attendant_db.list_consultas("p1")
    assert out["pending_part2"] is None
    assert out["pending_part2_modality"] is None
    assert out["has_completed"] is False


async def test_list_consultas_pending_part2_survives_part1_completed(patched_client, fake_client):
    """A 1ª parte pode já ter acontecido (completed, no passado) e sair da
    lista de consultas futuras — a pendência da 2ª parte não pode desaparecer
    só por isso (regressão: era calculada só a partir da lista futura)."""
    fake_client.store["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "status": "completed", "doctor_id": JULIO,
         "start_time": "2020-01-01T12:00:00+00:00", "end_time": "2020-01-01T13:00:00+00:00",
         "modality": "online", "session_note": "1ª consulta · parte 1 de 2"},
    ]
    out = await attendant_db.list_consultas("p1")
    assert out["appointments"] == []  # não aparece na lista futura
    assert out["pending_part2"] == "a1"
    assert out["pending_part2_modality"] == "online"
    assert out["has_completed"] is True


async def test_list_consultas_pending_part2_picks_most_recent_part1(patched_client, fake_client):
    base = {"patient_id": "p1", "doctor_id": JULIO, "session_note": "1ª consulta · parte 1 de 2"}
    fake_client.store["appointments"] = [
        {**base, "appointment_id": "old", "status": "completed",
         "start_time": "2020-01-01T12:00:00+00:00", "end_time": "2020-01-01T13:00:00+00:00"},
        {**base, "appointment_id": "new", "status": "scheduled",
         "start_time": "2099-01-01T12:00:00+00:00", "end_time": "2099-01-01T13:00:00+00:00"},
    ]
    out = await attendant_db.list_consultas("p1")
    assert out["pending_part2"] == "new"


async def test_list_consultas_billing(patched_client, fake_client):
    base = {"patient_id": "p1", "status": "scheduled", "doctor_id": JULIO,
            "start_time": "2099-10-05T12:00:00+00:00", "end_time": "2099-10-05T13:00:00+00:00"}
    fake_client.store["appointments"] = [
        {**base, "appointment_id": "a1", "is_courtesy": True, "booking_fee_waived": True},
        {**base, "appointment_id": "a2", "is_courtesy": False, "booking_fee_waived": True},
        {**base, "appointment_id": "a3"},
    ]
    out = await attendant_db.list_consultas("p1")
    assert [a["billing"] for a in out["appointments"]] == ["cortesia", "taxa_isenta", "normal"]


async def test_set_first_consultation(patched_client, fake_client):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "consultation_type": None}]
    await attendant_db.set_first_consultation("a1", True)
    assert fake_client.store["appointments"][0]["consultation_type"] == "primeira_consulta"
    await attendant_db.set_first_consultation("a1", False)
    assert fake_client.store["appointments"][0]["consultation_type"] == "acompanhamento"


async def test_get_appointment_doctor_id(patched_client, fake_client):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "doctor_id": JULIO}]
    assert await attendant_db.get_appointment_doctor_id("a1") == JULIO


async def test_get_appointment_doctor_id_missing(patched_client, fake_client):
    assert await attendant_db.get_appointment_doctor_id("nope") is None


def test_age_on():
    assert attendant_db.age_on("10/02/2016", date(2026, 2, 9)) == 9
    assert attendant_db.age_on("2016-02-10", date(2026, 2, 10)) == 10
    assert attendant_db.age_on("", date(2026, 1, 1)) is None
