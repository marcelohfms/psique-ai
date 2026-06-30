import pytest

import attendant_db


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


# ── Updates com whitelist ─────────────────────────────────────────────────────


async def test_update_patient_only_whitelisted(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "João", "secret": "x"}]
    await attendant_db.update_patient("p1", {"name": "João Silva", "secret": "HACK", "age": 30})
    row = patched_client.store["patients"][0]
    assert row["name"] == "João Silva"   # permitido
    assert row["secret"] == "x"          # ignorado (fora da whitelist)
    assert row["age"] == 30              # permitido


async def test_update_contact_whitelist(patched_client):
    patched_client.store["contacts"] = [{"id": "c1", "phone": "5581999998888", "name": "A"}]
    await attendant_db.update_contact("c1", {"name": "B", "manual_hold": True, "id": "EVIL"})
    row = patched_client.store["contacts"][0]
    assert row["name"] == "B"
    assert row["manual_hold"] is True
    assert row["id"] == "c1"             # id nunca é sobrescrito


async def test_update_link_whitelist(patched_client):
    patched_client.store["patient_contacts"] = [
        {"id": "pc1", "role": "agendamento", "is_self": False, "relationship": None},
    ]
    await attendant_db.update_link("pc1", {"role": "consulta", "relationship": "pai", "patient_id": "X"})
    row = patched_client.store["patient_contacts"][0]
    assert row["role"] == "consulta"
    assert row["relationship"] == "pai"
    assert "patient_id" not in row or row.get("patient_id") != "X"


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
