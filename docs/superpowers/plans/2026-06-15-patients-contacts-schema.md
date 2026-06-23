# Separação de Pacientes e Contatos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separar a tabela `users` (que mistura paciente + contato) em três tabelas — `patients`, `contacts`, `patient_contacts` — com relacionamento flexível por roles, sem quebrar o fluxo de agendamento em produção.

**Restrição importante:** a tabela `users` **nunca é apagada**. Ela é preservada como arquivo histórico de consulta. O plano apenas cria tabelas novas ao lado dela, lê dela no backfill e deixa de usá-la em código — sem `DROP`.

**Architecture:** Estratégia *strangler* com shim de compatibilidade. Primeiro criamos as novas tabelas e a nova camada de dados; depois reimplementamos `get_user_by_phone`/`upsert_user`/`get_users_by_phone` por cima das novas tabelas (retornando um dict "estilo user" mesclado) para que os ~40 call sites existentes continuem funcionando; por fim adicionamos o comportamento novo de roles (lembretes para todos os contatos com role `agendamento`, responsável financeiro, `is_self`). A migração de dados é idempotente e roda uma vez. Os call sites podem migrar para a API nativa de forma incremental em fases posteriores.

**Tech Stack:** Python 3 + Supabase (Postgres) async client, FastAPI, LangGraph, pytest com Supabase mockado via `tests/conftest.py` (`make_supabase_client`).

**Spec:** `docs/superpowers/specs/2026-06-15-patients-contacts-schema-design.md`

---

## File Structure

- **Create:** `supabase/migrations/20260615_create_patients_contacts.sql` — DDL das 3 novas tabelas + colunas em `appointments`
- **Create:** `app/patients.py` — nova camada de dados nativa (patients/contacts/patient_contacts, `resolve_active_patient`)
- **Create:** `scripts/migrate_users_to_patients_contacts.py` — backfill idempotente de `users` → novas tabelas
- **Create:** `tests/test_patients.py` — testes unitários da nova camada (Supabase mockado)
- **Modify:** `app/database.py` — reimplementar `get_user_by_phone`/`get_users_by_phone`/`upsert_user` como shim sobre as novas tabelas (Fase 4); remover `_SHARED_FIELDS`/`_phone_variants`-fallback quando os call sites migrarem (Fase 6, fora deste plano)
- **Modify:** `app/graph/tools.py` — disparo de lembretes/confirmações para todos os contatos com role `agendamento` (Fase 5)
- **Modify:** `app/graph/nodes.py` — usar `resolve_active_patient` na desambiguação multi-paciente (Fase 5)

> **Nota sobre `_phone_variants`:** a normalização de número (com/sem 9) continua necessária e se move para `app/patients.py`. A tabela `contacts.phone` é `UNIQUE` e sempre armazena a forma canônica (com 9).

---

## Phase 1 — Novas tabelas (DDL)

### Task 1: Migration das 3 tabelas + colunas de appointments

**Files:**
- Create: `supabase/migrations/20260615_create_patients_contacts.sql`

- [ ] **Step 1: Escrever o arquivo de migration**

```sql
-- Separação de pacientes e contatos.
-- Cria patients, contacts, patient_contacts e estende appointments.
-- As tabelas novas convivem com `users` até o backfill e o corte do shim.

CREATE TABLE IF NOT EXISTS patients (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT        NOT NULL,
    email                   TEXT,
    birth_date              DATE,
    age                     INT,
    doctor_id               UUID        REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    is_returning_patient    BOOL,
    consultation_reason     TEXT,
    referral_professional   TEXT,
    modality_restriction    TEXT        CHECK (modality_restriction IN ('online', 'presencial')),
    age_exception           BOOL        DEFAULT FALSE,
    custom_price            NUMERIC,
    booking_fee_waived      BOOL        DEFAULT FALSE,
    financial_name          TEXT,
    financial_cpf           TEXT,
    financial_email         TEXT,
    legacy_user_id          UUID,       -- rastreia a linha de origem em users (idempotência do backfill)
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
    id                              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone                           TEXT        UNIQUE NOT NULL,
    name                            TEXT,
    active                          BOOL        DEFAULT TRUE,
    manual_hold                     BOOL        DEFAULT FALSE,
    deactivated_at                  TIMESTAMPTZ,
    price_adjustment_notified_at    TIMESTAMPTZ,
    created_at                      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_contacts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id  UUID        NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    contact_id  UUID        NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role        TEXT        NOT NULL CHECK (role IN ('agendamento', 'financeiro', 'consulta')),
    is_self     BOOL        NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (patient_id, contact_id, role)
);

CREATE INDEX IF NOT EXISTS idx_pc_contact_role ON patient_contacts(contact_id, role);
CREATE INDEX IF NOT EXISTS idx_pc_patient_role ON patient_contacts(patient_id, role);

-- Estende appointments com patient_id/contact_id e flags que ainda não existem.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_id  UUID REFERENCES patients(id) ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS contact_id  UUID REFERENCES contacts(id) ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS booking_fee_waived BOOL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_appointments_patient_id ON appointments(patient_id);
```

> As demais colunas de `appointments` citadas no spec (`modality`, `paid_at`, `confirmed_at`, `reminder_*_sent_at`, `pos_consulta_sent_at`, `refund_*_at`, `reschedule_requested_at`, `pending_reschedule`, `consultation_type`) já existem hoje via migrations anteriores. Antes de aplicar, confirme com o Step 2; adicione `ADD COLUMN IF NOT EXISTS` apenas para as que faltarem.

- [ ] **Step 2: Verificar quais colunas já existem em appointments**

Run:
```bash
grep -rn "ADD COLUMN\|CREATE TABLE appointments\|modality\|paid_at\|confirmed_at\|reminder_day\|pos_consulta\|refund_\|pending_reschedule" supabase/migrations/ scripts/migrate_appointments.sql
```
Expected: lista as colunas já criadas. Para cada coluna do spec que NÃO aparecer, adicione uma linha `ALTER TABLE appointments ADD COLUMN IF NOT EXISTS <col> <tipo>;` no arquivo da migration. Não duplique colunas existentes.

- [ ] **Step 3: Aplicar a migration no Supabase**

Run: aplique via o fluxo de migrations usado no projeto (Supabase CLI ou painel SQL). Cole o conteúdo do arquivo no SQL editor do projeto Supabase e execute.
Expected: as 3 tabelas aparecem em `public` e `\d appointments` mostra `patient_id`, `contact_id`, `booking_fee_waived`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260615_create_patients_contacts.sql
git commit -m "feat: migration de patients, contacts e patient_contacts"
```

---

## Phase 2 — Camada de dados nativa (`app/patients.py`)

Toda esta fase usa o mock de Supabase existente. Veja `tests/conftest.py::make_supabase_client` — retorna `(client, table, execute)` encadeável; `execute` é um `AsyncMock` cujo `return_value=MagicMock(data=[...])` você configura por teste.

### Task 2: `normalize_phone` e `get_contact_by_phone`

**Files:**
- Create: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_patients.py
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
    # 55 + DDD(2) + 8 dígitos -> insere o 9
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.patients'`

- [ ] **Step 3: Implementação mínima**

```python
# app/patients.py
"""Camada de dados nativa para patients / contacts / patient_contacts.

Substitui gradualmente o modelo antigo de `users` (ver app/database.py).
"""
from app.database import get_supabase


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


def normalize_phone(phone: str) -> str:
    """Retorna a forma canônica do número de celular brasileiro (com o 9).

    Aceita formas com/sem o nono dígito e devolve sempre a versão com 9.
    Números que não casam o padrão BR são devolvidos só sem o sufixo do WhatsApp.
    """
    digits = _strip_phone(phone)
    if len(digits) == 13 and digits.startswith("55"):
        return digits  # já tem o 9
    if len(digits) == 12 and digits.startswith("55"):
        return digits[:4] + "9" + digits[4:]  # insere o 9 após o DDD
    return digits


async def get_contact_by_phone(phone: str) -> dict | None:
    """Retorna a linha de `contacts` para este número (forma canônica), ou None."""
    client = await get_supabase()
    canonical = normalize_phone(phone)
    result = (
        await client.from_("contacts")
        .select("*")
        .eq("phone", canonical)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat: app.patients com normalize_phone e get_contact_by_phone"
```

### Task 3: `get_patients_by_contact(contact_id, role=None)`

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
@pytest.mark.asyncio
async def test_get_patients_by_contact_filters_by_role():
    # patient_contacts join -> retorna patients vinculados com role 'agendamento'
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_get_patients_by_contact_filters_by_role -v`
Expected: FAIL — `AttributeError: module 'app.patients' has no attribute 'get_patients_by_contact'`

- [ ] **Step 3: Implementação mínima**

```python
async def get_patients_by_contact(contact_id: str, role: str | None = None) -> list[dict]:
    """Retorna os pacientes (dicts da tabela patients) vinculados a um contato.

    Quando `role` é informado, filtra pelo papel (agendamento/financeiro/consulta).
    Pacientes duplicados (mesmo paciente com vários papéis) são deduplicados por id.
    """
    client = await get_supabase()
    query = (
        client.from_("patient_contacts")
        .select("patient_id, role, is_self, patients(*)")
        .eq("contact_id", contact_id)
    )
    if role is not None:
        query = query.eq("role", role)
    result = await query.execute()

    seen: set[str] = set()
    out: list[dict] = []
    for row in (result.data or []):
        patient = row.get("patients")
        if patient and patient["id"] not in seen:
            seen.add(patient["id"])
            out.append(patient)
    return out
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat: get_patients_by_contact com filtro por role"
```

### Task 4: `get_contacts_for_patient(patient_id, role)` (destinatários de lembrete)

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
@pytest.mark.asyncio
async def test_get_contacts_for_patient_returns_all_agendamento_contacts():
    # pai e mãe ambos com role agendamento -> ambos recebem lembrete
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_get_contacts_for_patient_returns_all_agendamento_contacts -v`
Expected: FAIL — `AttributeError: ... has no attribute 'get_contacts_for_patient'`

- [ ] **Step 3: Implementação mínima**

```python
async def get_contacts_for_patient(patient_id: str, role: str) -> list[dict]:
    """Retorna os contatos ATIVOS com o papel `role` para um paciente.

    Usado para disparar lembretes/confirmações a todos os responsáveis
    (ex.: pai e mãe ambos com role 'agendamento').
    """
    client = await get_supabase()
    result = (
        await client.from_("patient_contacts")
        .select("contact_id, contacts(*)")
        .eq("patient_id", patient_id)
        .eq("role", role)
        .execute()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for row in (result.data or []):
        contact = row.get("contacts")
        if contact and contact.get("active") and contact["id"] not in seen:
            seen.add(contact["id"])
            out.append(contact)
    return out
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat: get_contacts_for_patient para disparo de lembretes por role"
```

### Task 5: `upsert_contact` e `upsert_patient`

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
@pytest.mark.asyncio
async def test_upsert_contact_inserts_when_absent():
    # primeira chamada (get) retorna vazio; insert retorna a nova linha
    insert_exec = AsyncMock(return_value=MagicMock(data=[{"id": "c-new", "phone": "5583988887777"}]))
    select_exec = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("select", "eq", "insert", "update"):
        getattr(table, m).return_value = table
    # select -> vazio, insert -> nova linha
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_upsert_contact_inserts_when_absent -v`
Expected: FAIL — `AttributeError: ... has no attribute 'upsert_contact'`

- [ ] **Step 3: Implementação mínima**

```python
async def upsert_contact(phone: str, data: dict) -> str | None:
    """Insere ou atualiza um contato pelo número canônico. Retorna o id."""
    client = await get_supabase()
    canonical = normalize_phone(phone)
    existing = await get_contact_by_phone(canonical)
    if existing:
        await client.from_("contacts").update(data).eq("id", existing["id"]).execute()
        return existing["id"]
    result = await client.from_("contacts").insert({"phone": canonical, **data}).execute()
    inserted = (result.data or [{}])[0]
    return inserted.get("id")


async def upsert_patient(data: dict, patient_id: str | None = None) -> str | None:
    """Insere um paciente novo ou atualiza um existente (por id). Retorna o id."""
    client = await get_supabase()
    if patient_id:
        await client.from_("patients").update(data).eq("id", patient_id).execute()
        return patient_id
    result = await client.from_("patients").insert(data).execute()
    inserted = (result.data or [{}])[0]
    return inserted.get("id")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat: upsert_contact e upsert_patient"
```

### Task 6: `link_patient_contact(patient_id, contact_id, role, is_self)`

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
@pytest.mark.asyncio
async def test_link_patient_contact_upserts_on_conflict():
    client, table, execute = _client_returning([{"id": "pc1"}])
    # adiciona o método upsert ao mock
    table.upsert.return_value = table
    with patch("app.patients.get_supabase", new_callable=AsyncMock, return_value=client):
        await patients.link_patient_contact("p1", "c1", "agendamento", is_self=True)
    table.upsert.assert_called_once()
    args, kwargs = table.upsert.call_args
    assert args[0]["patient_id"] == "p1"
    assert args[0]["role"] == "agendamento"
    assert args[0]["is_self"] is True
    # usa a UNIQUE(patient_id, contact_id, role) para evitar duplicatas
    assert kwargs.get("on_conflict") == "patient_id,contact_id,role"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_link_patient_contact_upserts_on_conflict -v`
Expected: FAIL — `AttributeError: ... has no attribute 'link_patient_contact'`

- [ ] **Step 3: Implementação mínima**

```python
async def link_patient_contact(
    patient_id: str, contact_id: str, role: str, is_self: bool = False
) -> None:
    """Vincula um contato a um paciente com um papel. Idempotente.

    Usa a constraint UNIQUE(patient_id, contact_id, role) — repetir a mesma
    ligação não cria duplicatas.
    """
    client = await get_supabase()
    await client.from_("patient_contacts").upsert(
        {
            "patient_id": patient_id,
            "contact_id": contact_id,
            "role": role,
            "is_self": is_self,
        },
        on_conflict="patient_id,contact_id,role",
    ).execute()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat: link_patient_contact idempotente"
```

### Task 7: `resolve_active_patient(phone)` — desambiguação por contexto

Implementa a Seção 2 do spec: 0 → None, 1 → direto, 2+ → paciente com agendamento próximo, senão sinaliza ambiguidade.

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
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
    # só p2 tem agendamento próximo -> assume p2
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py -k resolve_active_patient -v`
Expected: FAIL — `AttributeError: ... has no attribute 'resolve_active_patient'`

- [ ] **Step 3: Implementação mínima**

```python
from datetime import datetime, timezone


async def _patient_has_upcoming_appointment(patient_id: str) -> bool:
    """True se o paciente tem agendamento futuro/ongoing (status scheduled)."""
    client = await get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()
    result = (
        await client.from_("appointments")
        .select("id")
        .eq("patient_id", patient_id)
        .in_("status", ["scheduled", "pending_reschedule"])
        .gte("end_time", now_iso)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def resolve_active_patient(phone: str) -> dict:
    """Resolve qual paciente está em contexto para um número.

    Retorna sempre um dict com as chaves:
    - contact:    a linha de contacts, ou None se número desconhecido
    - patient:    o paciente em contexto, ou None
    - candidates: lista de pacientes (quando 2+ e ambíguo)
    - ambiguous:  True quando há 2+ candidatos e nenhum tem agendamento próximo

    Regras (Seção 2 do spec):
    - 0 pacientes  -> patient=None, ambiguous=False (onboarding)
    - 1 paciente   -> patient=esse, ambiguous=False
    - 2+ pacientes -> se exatamente um tem agendamento próximo, assume-o;
                      caso contrário ambiguous=True (caller pergunta qual).
    """
    contact = await get_contact_by_phone(phone)
    if not contact:
        return {"contact": None, "patient": None, "candidates": [], "ambiguous": False}

    candidates = await get_patients_by_contact(contact["id"], role="agendamento")
    if not candidates:
        return {"contact": contact, "patient": None, "candidates": [], "ambiguous": False}
    if len(candidates) == 1:
        return {"contact": contact, "patient": candidates[0], "candidates": candidates, "ambiguous": False}

    upcoming = [c for c in candidates if await _patient_has_upcoming_appointment(c["id"])]
    if len(upcoming) == 1:
        return {"contact": contact, "patient": upcoming[0], "candidates": candidates, "ambiguous": False}
    return {"contact": contact, "patient": None, "candidates": candidates, "ambiguous": True}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat: resolve_active_patient com desambiguação por agendamento próximo"
```

---

## Phase 3 — Backfill idempotente (`users` → novas tabelas)

### Task 8: Script de migração de dados

**Files:**
- Create: `scripts/migrate_users_to_patients_contacts.py`

- [ ] **Step 1: Escrever o script**

```python
"""Backfill idempotente: users -> patients + contacts + patient_contacts.

Idempotência:
- patients: usa patients.legacy_user_id == users.id (não recria)
- contacts: usa contacts.phone UNIQUE (não recria)
- patient_contacts: UNIQUE(patient_id, contact_id, role) via upsert

Roda uma vez após a migration da Phase 1. Seguro reexecutar.

Uso:
    uv run python scripts/migrate_users_to_patients_contacts.py [--dry-run]
"""
import asyncio
import sys

from app.database import get_supabase
from app.patients import normalize_phone, link_patient_contact

# Campos de users que pertencem ao paciente (clínicos).
_PATIENT_FIELDS = [
    "email", "birth_date", "age", "doctor_id", "is_returning_patient",
    "consultation_reason", "referral_professional", "modality_restriction",
    "age_exception", "custom_price", "booking_fee_waived",
    "financial_name", "financial_cpf", "financial_email",
]
# Campos de users que pertencem ao contato.
_CONTACT_FIELDS = [
    "active", "manual_hold", "deactivated_at", "price_adjustment_notified_at",
]
ROLES = ["agendamento", "financeiro", "consulta"]


def _patient_name(user: dict) -> str:
    # quando is_patient=False, o paciente é patient_name; senão é o name do contato
    if user.get("is_patient") is False and user.get("patient_name"):
        return user["patient_name"]
    return user.get("patient_name") or user.get("name") or "(sem nome)"


async def _get_or_create_contact(client, phone: str, name: str | None) -> str:
    canonical = normalize_phone(phone)
    existing = await client.from_("contacts").select("id").eq("phone", canonical).execute()
    if existing.data:
        return existing.data[0]["id"]
    inserted = await client.from_("contacts").insert({"phone": canonical, "name": name}).execute()
    return inserted.data[0]["id"]


async def _get_or_create_patient(client, user: dict) -> str:
    existing = await client.from_("patients").select("id").eq("legacy_user_id", user["id"]).execute()
    if existing.data:
        return existing.data[0]["id"]
    payload = {"name": _patient_name(user), "legacy_user_id": user["id"]}
    for f in _PATIENT_FIELDS:
        if user.get(f) is not None:
            payload[f] = user[f]
    inserted = await client.from_("patients").insert(payload).execute()
    return inserted.data[0]["id"]


async def main(dry_run: bool) -> None:
    client = await get_supabase()
    users = (await client.from_("users").select("*").execute()).data or []
    print(f"{len(users)} users encontrados")

    for user in users:
        phone = user.get("number")
        if not phone:
            print(f"  SKIP user {user['id']} sem number")
            continue
        name = _patient_name(user)
        is_self = bool(user.get("is_patient"))
        if dry_run:
            print(f"  DRY user {user['id']} -> contact({normalize_phone(phone)}) "
                  f"+ patient({name}) is_self={is_self}")
            continue

        contact_id = await _get_or_create_contact(client, phone, user.get("name"))
        # propaga campos de contato (último a escrever vence — aceitável: shared antes)
        contact_update = {f: user[f] for f in _CONTACT_FIELDS if user.get(f) is not None}
        if contact_update:
            await client.from_("contacts").update(contact_update).eq("id", contact_id).execute()

        patient_id = await _get_or_create_patient(client, user)
        for role in ROLES:
            await link_patient_contact(patient_id, contact_id, role, is_self=is_self)
        print(f"  OK user {user['id']} -> patient {patient_id} / contact {contact_id}")

    # Backfill de appointments.patient_id a partir de legacy_user_id
    if not dry_run:
        print("Atualizando appointments.patient_id...")
        appts = (await client.from_("appointments").select("id, user_id").execute()).data or []
        for appt in appts:
            if not appt.get("user_id"):
                continue
            p = await client.from_("patients").select("id").eq("legacy_user_id", appt["user_id"]).execute()
            if p.data:
                await client.from_("appointments").update(
                    {"patient_id": p.data[0]["id"]}
                ).eq("id", appt["id"]).execute()
        print("appointments atualizados")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
```

- [ ] **Step 2: Rodar em dry-run**

Run: `uv run python scripts/migrate_users_to_patients_contacts.py --dry-run`
Expected: imprime "N users encontrados" e uma linha `DRY ...` por usuário, sem escrever no banco. Confira que `is_self` e os nomes batem com a expectativa para alguns casos conhecidos (ex.: um responsável deve sair com `is_self=False`).

- [ ] **Step 3: Rodar de verdade**

Run: `uv run python scripts/migrate_users_to_patients_contacts.py`
Expected: linhas `OK ...` para cada usuário e "appointments atualizados".

- [ ] **Step 4: Validar idempotência (rodar de novo)**

Run: `uv run python scripts/migrate_users_to_patients_contacts.py`
Expected: roda sem erros e sem criar duplicatas. Verifique no Supabase: `SELECT count(*) FROM patients` == nº de users distintos; `SELECT count(*) FROM contacts` == nº de phones distintos; nenhuma linha duplicada em `patient_contacts`.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_users_to_patients_contacts.py
git commit -m "feat: script idempotente de backfill users -> patients/contacts"
```

---

## Phase 4 — Shim de compatibilidade em `app/database.py`

Reimplementa as 3 funções públicas que os ~40 call sites usam, agora lendo/escrevendo nas novas tabelas, mas devolvendo um dict "estilo user" mesclado (`patient` ∪ `contact`). Isso mantém os call sites funcionando sem alterá-los ainda.

> **Importante:** o `id` exposto pelo shim passa a ser o `patient_id` (porque `appointments` e `user_db_id` são usados como referência de paciente no código atual). O número/phone vem do `contact`.

### Task 9: Reimplementar `get_users_by_phone` / `get_user_by_phone` como shim

**Files:**
- Modify: `app/database.py:52-79`
- Test: `tests/test_database_shim.py` (Create)

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_database_shim.py
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
    # um "user" por paciente, com id = patient_id e campos de contato mesclados
    assert {r["id"] for r in rows} == {"p1", "p2"}
    assert all(r["number"] == "5583988887777" for r in rows)
    assert all(r["active"] is True for r in rows)


@pytest.mark.asyncio
async def test_get_user_by_phone_returns_none_when_unknown():
    with patch("app.database.get_contact_by_phone", new_callable=AsyncMock, return_value=None):
        assert await database.get_user_by_phone("5583988887777") is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_database_shim.py -v`
Expected: FAIL — o `get_users_by_phone` antigo consulta a tabela `users` e não casa as asserts (ou erra ao mockar).

- [ ] **Step 3: Reimplementar o shim**

Em `app/database.py`, adicione o import no topo (após os imports existentes):

```python
from app.patients import (
    get_contact_by_phone,
    get_patients_by_contact,
    upsert_contact,
    upsert_patient,
    link_patient_contact,
)
```

Substitua o corpo de `get_users_by_phone` (linhas 52-68) por:

```python
async def get_users_by_phone(phone: str) -> list[dict]:
    """[shim] Retorna um dict 'estilo user' por paciente vinculado a este número.

    Mescla a linha de `contacts` (number/active/manual_hold/...) com cada
    `patients`. `id` é o patient_id. Mantido para compatibilidade com call
    sites legados; novo código deve usar app.patients.resolve_active_patient.
    """
    contact = await get_contact_by_phone(phone)
    if not contact:
        return []
    pats = await get_patients_by_contact(contact["id"])
    rows: list[dict] = []
    for p in pats:
        merged = {**contact, **p}      # campos do paciente vencem em conflito
        merged["id"] = p["id"]         # id = patient_id (refs de appointments/user_db_id)
        merged["number"] = contact["phone"]
        merged["_contact_id"] = contact["id"]
        rows.append(merged)
    return rows
```

`get_user_by_phone` (linhas 71-79) permanece igual — ele já chama `get_users_by_phone` e escolhe o ativo. Verifique que continua assim; se ainda referenciar a tabela `users` diretamente, ajuste para usar `get_users_by_phone`.

Remova `_phone_variants` e `_strip_phone` SE não houver mais usos em `app/database.py` após a mudança (`grep -n "_phone_variants\|_strip_phone" app/database.py`); caso `log_event`/`save_message` ainda usem `_strip_phone`, mantenha-o.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_database_shim.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `uv run pytest --tb=short`
Expected: ver quais testes legados quebram por causa do shim. Anote-os para a Task 10.

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_database_shim.py
git commit -m "refactor: get_users_by_phone como shim sobre contacts/patients"
```

### Task 10: Reimplementar `upsert_user` como shim

**Files:**
- Modify: `app/database.py:82-151`
- Test: `tests/test_database_shim.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
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
    # 'active' foi para o contato; 'email'/'doctor_id' foram para o paciente
    assert captured["contact_data"].get("active") is False
    assert captured["patient_data"].get("email") == "j@x.com"
    assert "active" not in captured["patient_data"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_database_shim.py::test_upsert_user_routes_fields_to_patient_and_contact -v`
Expected: FAIL — o `upsert_user` atual escreve na tabela `users`.

- [ ] **Step 3: Reimplementar o shim**

Substitua todo o corpo de `upsert_user` (linhas 82-151) por:

```python
# Campos que pertencem ao CONTATO (o resto vai para o paciente).
_CONTACT_FIELDS = {
    "active", "manual_hold", "deactivated_at",
    "price_adjustment_notified_at", "name",
}


async def upsert_user(phone: str, data: dict, user_id: str | None = None) -> str | None:
    """[shim] Roteia campos para patients/contacts e devolve o patient_id.

    - Campos em _CONTACT_FIELDS vão para `contacts` (compartilhados pelo número).
    - Demais campos vão para `patients`.
    - `user_id`, quando presente, é tratado como patient_id (mesma semântica
      que o user_db_id legado).
    - Garante contato + vínculo agendamento/financeiro/consulta para pacientes novos.
    """
    contact_data = {k: v for k, v in data.items() if k in _CONTACT_FIELDS}
    patient_data = {k: v for k, v in data.items() if k not in _CONTACT_FIELDS}

    # 'name' é ambíguo: é o nome do contato; o nome do paciente é patient_name.
    if "patient_name" in patient_data:
        patient_data["name"] = patient_data.pop("patient_name")
    elif "name" in data and user_id is None:
        # paciente novo sem patient_name: usa o name como nome do paciente também
        patient_data.setdefault("name", data["name"])

    contact_id = await upsert_contact(phone, contact_data or {"name": data.get("name")})

    patient_id = await upsert_patient(patient_data, patient_id=user_id) if (patient_data or not user_id) else user_id

    if patient_id and contact_id:
        is_self = data.get("is_patient")
        for role in ("agendamento", "financeiro", "consulta"):
            await link_patient_contact(
                patient_id, contact_id, role,
                is_self=bool(is_self) if is_self is not None else False,
            )
    return patient_id
```

> **Nota sobre `is_patient`:** o modelo antigo guarda `is_patient` em `users`; no novo, vira `patient_contacts.is_self`. O shim traduz isso ao linkar. Campos legados como `patient_name`/`is_patient` que não existem em `patients` NÃO devem ser gravados na tabela `patients` — confirme que `patient_data` não os contém antes do insert (o código acima já remove `patient_name`; adicione `patient_data.pop("is_patient", None)` antes do upsert).

Adicione a linha `patient_data.pop("is_patient", None)` logo após o bloco de tratamento de `name`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_database_shim.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `uv run pytest --tb=short`
Expected: PASS. Conserte testes legados que assumiam a estrutura `users` (atualize mocks de `get_supabase` para mockar `get_contact_by_phone`/`get_patients_by_contact`, ou ajuste asserts). Mostre cada falha e corrija antes de prosseguir.

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_database_shim.py
git commit -m "refactor: upsert_user como shim roteando para patients/contacts"
```

---

## Phase 5 — Comportamento novo de roles

### Task 11: Lembretes/confirmações para todos os contatos com role `agendamento`

O disparo de lembretes hoje envia para um único número. Localize a função de lembrete e troque o destinatário único por iteração sobre `get_contacts_for_patient(patient_id, 'agendamento')`.

**Files:**
- Modify: `app/graph/tools.py` (função de lembrete/confirmação — localizar no Step 1)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Localizar o ponto de disparo**

Run:
```bash
grep -n "reminder_day_before_sent_at\|reminder_day_of_sent_at\|send_text\|confirmar presença\|lembrete" app/graph/tools.py
```
Expected: identifica a(s) função(ões) que enviam lembrete/confirmação para um número. Anote a linha exata.

- [ ] **Step 2: Escrever o teste que falha**

```python
# tests/test_tools.py — novo teste
@pytest.mark.asyncio
async def test_reminder_sent_to_all_agendamento_contacts():
    from app.graph import tools
    contacts = [{"id": "cpai", "phone": "5583111"}, {"id": "cmae", "phone": "5583222"}]
    with patch("app.graph.tools.get_contacts_for_patient", new_callable=AsyncMock,
               return_value=contacts), \
         patch("app.whatsapp.send_text", new_callable=AsyncMock) as send:
        await tools.send_appointment_reminder(patient_id="p1", appointment={"start_time": "2026-06-20T14:00:00Z"})
    sent_to = {c.args[0] for c in send.call_args_list}
    assert sent_to == {"5583111", "5583222"}
```

> Ajuste o nome `send_appointment_reminder` e a assinatura ao que existir no código (Step 1). Se hoje o lembrete vive dentro de outra função maior, extraia uma função `send_appointment_reminder(patient_id, appointment)` testável e chame-a a partir do lugar antigo.

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py::test_reminder_sent_to_all_agendamento_contacts -v`
Expected: FAIL

- [ ] **Step 4: Implementar o disparo multi-contato**

No topo de `app/graph/tools.py`, adicione ao import de `app.patients`:
```python
from app.patients import get_contacts_for_patient
```
Refatore o disparo para iterar:
```python
async def send_appointment_reminder(patient_id: str, appointment: dict) -> None:
    """Envia o lembrete para TODOS os contatos com role 'agendamento' do paciente."""
    from app.whatsapp import send_text
    contacts = await get_contacts_for_patient(patient_id, "agendamento")
    text = _format_reminder_text(appointment)  # reutiliza a formatação existente
    for contact in contacts:
        await send_text(contact["phone"], text)
```
Substitua o envio único antigo por uma chamada a `send_appointment_reminder`. Mantenha a marcação de `reminder_*_sent_at` no appointment como está (uma vez por appointment, não por contato).

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k reminder -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat: lembrete de consulta disparado a todos os contatos de agendamento"
```

### Task 12: Confirmação idempotente (primeiro a confirmar vence)

**Files:**
- Modify: `app/graph/tools.py` (função `confirm_appointment` — localizar)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Localizar a confirmação**

Run: `grep -n "confirmed_at\|confirm_appointment" app/graph/tools.py`
Expected: linha da função de confirmação.

- [ ] **Step 2: Escrever o teste que falha**

```python
@pytest.mark.asyncio
async def test_confirm_appointment_is_idempotent():
    from app.graph import tools
    # appointment já confirmado -> segunda confirmação não regrava confirmed_at
    client, table, execute = make_supabase_client()
    execute.return_value = MagicMock(data=[{"id": "a1", "confirmed_at": "2026-06-19T10:00:00Z"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client):
        already = await tools.is_appointment_confirmed("a1")
    assert already is True
```

> Importe `make_supabase_client` no topo do teste: `from tests.conftest import make_supabase_client`.

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py::test_confirm_appointment_is_idempotent -v`
Expected: FAIL — `is_appointment_confirmed` não existe.

- [ ] **Step 4: Implementar guarda de idempotência**

```python
async def is_appointment_confirmed(appointment_db_id: str) -> bool:
    """True se o appointment já tem confirmed_at (evita dupla confirmação)."""
    client = await get_supabase()
    result = (
        await client.from_("appointments")
        .select("confirmed_at")
        .eq("id", appointment_db_id)
        .maybe_single()
        .execute()
    )
    return bool(result.data and result.data.get("confirmed_at"))
```
Na função de confirmação existente, antes de gravar `confirmed_at`, faça `if await is_appointment_confirmed(id): return <mensagem "já confirmado">`.

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k confirm -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat: confirmação de consulta idempotente (primeiro a confirmar vence)"
```

### Task 13: Desambiguação multi-paciente usa `resolve_active_patient`

Hoje `nodes.py` faz a desambiguação via `get_users_by_phone` + `pending_patients`. Troque a decisão "qual paciente" pela regra de contexto (`resolve_active_patient`), mantendo `pending_patients` apenas para o caso ambíguo.

**Files:**
- Modify: `app/graph/nodes.py:75-237`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_process_message.py — novo teste
@pytest.mark.asyncio
async def test_disambiguation_assumes_patient_with_upcoming_appointment():
    from app.graph import nodes
    resolved = {
        "contact": {"id": "c1"},
        "patient": {"id": "p2", "name": "Maria"},
        "candidates": [{"id": "p1"}, {"id": "p2"}],
        "ambiguous": False,
    }
    with patch("app.graph.nodes.resolve_active_patient", new_callable=AsyncMock, return_value=resolved):
        state = {"phone": "5583988887777", "messages": [], "stage": "collect_info"}
        update = await nodes._resolve_patient_context(state)  # helper extraído
    assert update["user_db_id"] == "p2"
    assert update.get("pending_patients") is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_process_message.py::test_disambiguation_assumes_patient_with_upcoming_appointment -v`
Expected: FAIL — `_resolve_patient_context` não existe.

- [ ] **Step 3: Extrair e implementar o helper**

Em `app/graph/nodes.py`, adicione ao import (linha 21): `from app.patients import resolve_active_patient`.
Crie a função:
```python
async def _resolve_patient_context(state: dict) -> dict:
    """Decide o paciente em contexto. Retorna updates parciais de estado.

    - resolvido (0/1/auto): seta user_db_id e limpa pending_patients
    - ambíguo (2+ sem agendamento próximo): seta pending_patients (caller pergunta)
    """
    resolved = await resolve_active_patient(state["phone"])
    if resolved["ambiguous"]:
        return {"pending_patients": resolved["candidates"]}
    patient = resolved["patient"]
    return {
        "user_db_id": patient["id"] if patient else None,
        "pending_patients": None,
    }
```
No ponto da desambiguação (linhas ~75-237), use `_resolve_patient_context` para decidir, em vez da lógica ad-hoc baseada só em `get_users_by_phone`. Mantenha o texto da pergunta de seleção quando `pending_patients` vier preenchido.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_process_message.py -k disambiguation -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `uv run pytest --tb=short`
Expected: PASS. Conserte testes de fluxo que dependiam do comportamento antigo de `pending_patients`.

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "refactor: desambiguação multi-paciente via resolve_active_patient"
```

---

## Phase 6 — Endurecimento e limpeza (após validação em produção)

> Estas tasks só depois que o shim estiver estável em produção por alguns dias. Cada uma é opcional e independente; podem virar um plano próprio.

### Task 14: Apontar `appointments` para `patient_id` no código

**Files:**
- Modify: `app/database.py:223-265` (`get_upcoming_appointments`), `app/graph/tools.py` (queries `.eq("user_id", ...)`)
- Test: `tests/test_tools.py`, `tests/test_database_shim.py`

- [ ] **Step 1:** `grep -n '"user_id"\|user_id' app/graph/tools.py app/database.py` para listar as queries que ainda filtram por `appointments.user_id`.
- [ ] **Step 2:** Para cada uma, escrever/ajustar o teste esperando filtro por `patient_id` (o id do shim já é o patient_id, então a troca é direta).
- [ ] **Step 3:** Trocar `.eq("user_id", X)` por `.eq("patient_id", X)` nessas queries.
- [ ] **Step 4:** `uv run pytest --tb=short` — Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "refactor: queries de appointments por patient_id"`

### Task 15: Migrar call sites legados para a API nativa (incremental)

**Files:** `app/graph/tools.py`, `app/graph/nodes.py`, `app/main.py`, `app/media.py`

- [ ] **Step 1:** Listar call sites: `grep -rn "get_user_by_phone\|upsert_user\|get_users_by_phone" app/ | grep -v worktree`.
- [ ] **Step 2:** Migrar um arquivo por vez para `resolve_active_patient`/`upsert_patient`/`upsert_contact`, rodando `uv run pytest --tb=short` após cada arquivo.
- [ ] **Step 3:** Quando nenhum call site usar mais o shim, remover `get_user_by_phone`/`get_users_by_phone`/`upsert_user` de `app/database.py`.
- [ ] **Step 4:** **Não apagar a tabela `users`.** Ela é preservada como arquivo histórico/de consulta. Após confirmar que nada mais lê `users` em código, ela apenas deixa de ser usada — permanece no banco intacta.

---

## Verificação final

- [ ] `uv run pytest --tb=short` — toda a suíte passa
- [ ] Smoke manual: enviar mensagem de um número conhecido → bot reconhece o paciente; número com 2 pacientes e 1 agendamento próximo → assume o certo; número novo → onboarding cria patient+contact+links
- [ ] Conferir no Supabase que um agendamento novo grava `patient_id` e `contact_id`
- [ ] Conferir que um lembrete chega a pai E mãe quando ambos têm role `agendamento`
