# Painel da Atendente no Chatwoot — Fase 1 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar ao app `dashboard/` um painel embutível no Chatwoot onde a atendente edita direto no banco o cadastro do paciente/contato + flags clínicas, e reseta o checkpoint da Eva com um clique.

**Architecture:** Novas rotas no `dashboard/` (FastAPI). A resolução de paciente e os updates ficam num módulo autocontido (`dashboard/attendant_db.py`) que usa o Supabase direto — **sem importar `app/`** (a imagem Docker do dashboard não contém `app/`). O front lê o telefone do contato via `postMessage` do Chatwoot (ou `?phone=` para teste). O reset apaga as 3 tabelas de checkpoint por `thread_id` (`<dígitos>@s.whatsapp.net`, todas as variantes com/sem 9).

**Tech Stack:** FastAPI, Jinja2, Tailwind (CDN), Supabase (postgrest async), pytest + pytest-asyncio, Starlette TestClient.

**Escopo:** Apenas Fase 1 do spec (`docs/superpowers/specs/2026-06-30-painel-atendente-chatwoot-design.md`). Agendamentos (Fases 2–3) ficam de fora. O embed mínimo no Chatwoot (handshake `postMessage` + headers de iframe) está incluído para a entrega ser usável de verdade.

**Pré-requisito de leitura:** `dashboard/main.py` (padrões de auth/Supabase existentes), `app/patients.py` (`_phone_variants`/normalização), `app/main.py:559-573` (`_reset_conversation`, formato do `thread_id`).

**Referência Chatwoot Dashboard Apps:** https://www.chatwoot.com/hc/user-guide/articles/1677691702-how-to-use-dashboard-apps — protocolo confirmado: o iframe envia `chatwoot-dashboard-app:fetch-info` e o Chatwoot responde via `message` com `event.data` sendo uma **string JSON** no formato `{ event: "appContext", data: { conversation, contact, currentAgent } }`. **A verificar no teste manual (Task 11):** o nome exato do campo do telefone em `data.contact` (assumido `phone_number`).

---

## Estrutura de arquivos

- **Criar** `dashboard/db_client.py` — getter lazy do cliente Supabase, compartilhável e testável.
- **Criar** `dashboard/attendant_db.py` — camada de dados do painel (resolução + updates + reset + log).
- **Criar** `dashboard/attendant_routes.py` — `APIRouter` com as rotas do painel (auth por token).
- **Criar** `dashboard/templates/atendente.html` — UI (postMessage, seletor, formulários, botão reset).
- **Modificar** `dashboard/main.py` — incluir o router; permitir embed em iframe (headers).
- **Modificar** `dashboard/pyproject.toml` — dev deps de teste.
- **Criar** `dashboard/tests/conftest.py` — fake Supabase + fixtures.
- **Criar** `dashboard/tests/test_attendant_db.py`, `dashboard/tests/test_attendant_routes.py`.
- **Modificar** `.env.example` — `ATTENDANT_PANEL_TOKEN`.

Rodar testes (a partir de `dashboard/`): `uv run pytest -q`

---

## Task 0: Scaffolding de testes do dashboard

**Files:**
- Modify: `dashboard/pyproject.toml`
- Create: `dashboard/tests/__init__.py`
- Create: `dashboard/tests/conftest.py`
- Create: `dashboard/pytest.ini`

- [ ] **Step 1: Adicionar dev deps**

Em `dashboard/pyproject.toml`, após o bloco `dependencies = [...]`, acrescentar:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
]
```

- [ ] **Step 2: Config do pytest**

Criar `dashboard/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: Pacote de testes vazio**

Criar `dashboard/tests/__init__.py` vazio.

- [ ] **Step 4: Fake Supabase + fixtures**

Criar `dashboard/tests/conftest.py`:

```python
import os
import pytest

# Env mínimo para importar os módulos do dashboard sem inicializar nada real.
os.environ.setdefault("SUPABASE_URL", "http://fake.local")
os.environ.setdefault("SUPABASE_KEY", "fake-key")
os.environ.setdefault("ATTENDANT_PANEL_TOKEN", "test-token")


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Imita o query-builder do postgrest-py para os usos do painel."""
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters = []  # list[tuple[col, val]]

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def _matches(self, row):
        return all(row.get(c) == v for c, v in self._filters)

    async def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "select":
            return FakeResult([r for r in rows if self._matches(r)])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows.append(dict(p))
            return FakeResult([dict(p) for p in payload])
        if self._op == "update":
            changed = []
            for r in rows:
                if self._matches(r):
                    r.update(self._payload)
                    changed.append(dict(r))
            return FakeResult(changed)
        if self._op == "delete":
            kept, removed = [], []
            for r in rows:
                (removed if self._matches(r) else kept).append(r)
            self._store[self._table] = kept
            return FakeResult(removed)
        return FakeResult([])


class FakeClient:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def from_(self, table):
        return FakeQuery(self.store, table)


@pytest.fixture
def fake_client():
    return FakeClient()
```

- [ ] **Step 5: Verificar que a coleta funciona**

Run: `cd dashboard && uv run pytest -q`
Expected: `no tests ran` (sem erros de import/config).

- [ ] **Step 6: Commit**

```bash
git add dashboard/pyproject.toml dashboard/pytest.ini dashboard/tests/__init__.py dashboard/tests/conftest.py
git commit -m "test(dashboard): scaffolding de pytest + fake Supabase"
```

---

## Task 1: Getter do cliente Supabase compartilhável

**Files:**
- Create: `dashboard/db_client.py`

- [ ] **Step 1: Implementar**

Criar `dashboard/db_client.py`:

```python
"""Getter lazy do cliente Supabase para os módulos do painel da atendente.

Mantido separado de main.py para evitar import circular (main importa as rotas,
as rotas importam a camada de dados, que precisa do cliente).
"""
import os
from supabase import AsyncClient, acreate_client

_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _client
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/db_client.py
git commit -m "feat(dashboard): getter lazy do cliente Supabase"
```

---

## Task 2: Variantes de telefone

**Files:**
- Create: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

> Os testes rodam com `cd dashboard && uv run pytest`, então `dashboard/` fica no `sys.path` (há `tests/__init__.py`, então o pai do pacote `tests` é inserido). Os imports são diretos: `import attendant_db`, `import main`, etc. — **sem** o prefixo `dashboard.`.

Criar `dashboard/tests/test_attendant_db.py`:

```python
import attendant_db


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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'attendant_db'`).

- [ ] **Step 3: Implementar o mínimo**

Criar `dashboard/attendant_db.py`:

```python
"""Camada de dados do painel da atendente (Fase 1).

Autocontida: replica as poucas queries necessárias usando o cliente Supabase
do dashboard. NÃO importa app/ (a imagem Docker do dashboard não contém app/).
"""
from datetime import datetime, timezone

from db_client import get_client


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "").lstrip("+")


def _phone_variants(phone: str) -> list[str]:
    """Variantes com e sem o 9 de um celular brasileiro. Espelha app/database.py."""
    digits = _strip_phone(phone)
    if len(digits) == 13 and digits.startswith("55"):
        return [digits, digits[:4] + digits[5:]]
    if len(digits) == 12 and digits.startswith("55"):
        return [digits[:4] + "9" + digits[4:], digits]
    return [digits]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): variantes de telefone do painel"
```

---

## Task 3: Resolver contato + pacientes por telefone

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `dashboard/tests/test_attendant_db.py`:

```python
import pytest


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
    # contato salvo sem o 9; telefone consultado vem com o 9
    patched_client.store["contacts"] = [
        {"id": "c2", "phone": "558199998888", "name": "Ana"},
    ]
    out = await attendant_db.resolve_contact_and_patients("5581999998888")
    assert out["contact"]["id"] == "c2"
    assert out["patients"] == []


async def test_resolve_no_contact(patched_client):
    out = await attendant_db.resolve_contact_and_patients("5581900000000")
    assert out == {"contact": None, "patients": []}
```

> Nota sobre o fake: `select("contacts").eq("phone", v)` filtra por igualdade exata. Como `resolve` tenta as variantes em ordem, o teste com variante exercita o segundo `eq`. O join `patient_contacts(..., patients(*))` é simulado embutindo a chave `"patients"` na linha (igual ao retorno aninhado do postgrest).

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: FAIL (`AttributeError: resolve_contact_and_patients`).

- [ ] **Step 3: Implementar**

Acrescentar a `dashboard/attendant_db.py`:

```python
async def _get_contact_by_phone(client, phone: str) -> dict | None:
    for variant in _phone_variants(phone):
        res = await client.from_("contacts").select("*").eq("phone", variant).execute()
        rows = res.data or []
        if rows:
            return rows[0]
    return None


async def _get_patients_by_contact(client, contact_id: str) -> list[dict]:
    res = (
        await client.from_("patient_contacts")
        .select("patient_id, role, is_self, patients(*)")
        .eq("contact_id", contact_id)
        .execute()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for row in (res.data or []):
        patient = row.get("patients")
        if patient and patient["id"] not in seen:
            seen.add(patient["id"])
            out.append(patient)
    return out


async def resolve_contact_and_patients(phone: str) -> dict:
    """Retorna {"contact": <row|None>, "patients": [<row>, ...]} para um telefone."""
    client = await get_client()
    contact = await _get_contact_by_phone(client, phone)
    if not contact:
        return {"contact": None, "patients": []}
    patients = await _get_patients_by_contact(client, contact["id"])
    return {"contact": contact, "patients": patients}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): resolver contato + pacientes por telefone"
```

---

## Task 4: Buscar paciente + vínculo

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: FAIL (`AttributeError: get_patient`).

- [ ] **Step 3: Implementar**

Acrescentar:

```python
async def get_patient(patient_id: str) -> dict | None:
    client = await get_client()
    res = await client.from_("patients").select("*").eq("id", patient_id).execute()
    rows = res.data or []
    return rows[0] if rows else None


async def get_link(patient_id: str, contact_id: str) -> dict | None:
    client = await get_client()
    res = (
        await client.from_("patient_contacts")
        .select("*")
        .eq("patient_id", patient_id)
        .eq("contact_id", contact_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): buscar paciente e vínculo"
```

---

## Task 5: Updates (contato, paciente, vínculo) com whitelist de campos

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: FAIL (`AttributeError: update_patient`).

- [ ] **Step 3: Implementar**

Acrescentar:

```python
_CONTACT_FIELDS = {"name", "cpf", "phone", "active", "manual_hold"}
_PATIENT_FIELDS = {
    "name", "birth_date", "age", "patient_cpf", "email", "doctor_id",
    "is_returning_patient", "modality_restriction", "age_exception", "custom_price",
    "financial_name", "financial_cpf", "financial_email",
}
_LINK_FIELDS = {"role", "is_self", "relationship"}


def _filter(data: dict, allowed: set[str]) -> dict:
    return {k: v for k, v in data.items() if k in allowed}


async def update_contact(contact_id: str, data: dict) -> None:
    payload = _filter(data, _CONTACT_FIELDS)
    if not payload:
        return
    client = await get_client()
    await client.from_("contacts").update(payload).eq("id", contact_id).execute()


async def update_patient(patient_id: str, data: dict) -> None:
    payload = _filter(data, _PATIENT_FIELDS)
    if not payload:
        return
    client = await get_client()
    await client.from_("patients").update(payload).eq("id", patient_id).execute()


async def update_link(pc_id: str, data: dict) -> None:
    payload = _filter(data, _LINK_FIELDS)
    if not payload:
        return
    client = await get_client()
    await client.from_("patient_contacts").update(payload).eq("id", pc_id).execute()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): updates com whitelist de campos"
```

---

## Task 6: Log de auditoria

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: FAIL (`AttributeError: log_event`).

- [ ] **Step 3: Implementar**

Acrescentar (espelha `app/database.py:log_event`, fire-and-forget):

```python
async def log_event(event_type: str, phone: str, metadata: dict | None = None) -> None:
    try:
        client = await get_client()
        await client.from_("events").insert({
            "event_type": event_type,
            "phone": _strip_phone(phone),
            "metadata": metadata or {},
        }).execute()
    except Exception:
        pass  # auditoria nunca quebra o fluxo principal
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): log de auditoria em events"
```

---

## Task 7: Reset do checkpoint

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

> O `thread_id` é `<dígitos>@s.whatsapp.net`, e os dígitos podem estar na variante com OU sem o 9 (ver `app/main.py:646`). Apagamos as 3 tabelas para todas as variantes + sufixo.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar:

```python
async def test_reset_checkpoint_deletes_all_tables_and_variants(patched_client):
    # thread_id gravado na variante COM o 9
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: FAIL (`AttributeError: reset_checkpoint`).

- [ ] **Step 3: Implementar**

Acrescentar:

```python
_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")


async def reset_checkpoint(phone: str) -> int:
    """Apaga as linhas de checkpoint da Eva para um telefone (todas as variantes).

    Retorna o total de linhas removidas nas 3 tabelas. Cada DELETE é isolado em
    try/except — uma tabela ausente não impede as outras.
    """
    client = await get_client()
    thread_ids = [v + "@s.whatsapp.net" for v in _phone_variants(phone)]
    total = 0
    for table in _CHECKPOINT_TABLES:
        for tid in thread_ids:
            try:
                res = await client.from_(table).delete().eq("thread_id", tid).execute()
                total += len(res.data or [])
            except Exception:
                pass
    return total
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(dashboard): reset do checkpoint por telefone (todas as variantes)"
```

---

## Task 8: Auth por token + rotas de leitura

**Files:**
- Create: `dashboard/attendant_routes.py`
- Test: `dashboard/tests/test_attendant_routes.py`

> Endpoints testados com `starlette.testclient.TestClient` montando um app mínimo que inclui só o router. As funções de `attendant_db` são monkeypatchadas (sem Supabase).

- [ ] **Step 1: Escrever o teste que falha**

Criar `dashboard/tests/test_attendant_routes.py`:

```python
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import attendant_routes
import attendant_db


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(attendant_routes.router)
    return TestClient(app)


def test_resolve_requires_token(client):
    r = client.get("/api/atendente/resolve", params={"phone": "5581999998888"})
    assert r.status_code == 401


def test_resolve_wrong_token(client):
    r = client.get("/api/atendente/resolve",
                   params={"phone": "5581999998888", "token": "errado"})
    assert r.status_code == 401


def test_resolve_ok(client, monkeypatch):
    async def fake_resolve(phone):
        return {"contact": {"id": "c1", "name": "Maria"}, "patients": [{"id": "p1", "name": "João"}]}
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)
    r = client.get("/api/atendente/resolve",
                   params={"phone": "5581999998888", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["contact"]["id"] == "c1"
    assert body["patients"][0]["id"] == "p1"


def test_get_patient_ok(client, monkeypatch):
    async def fake_get_patient(pid):
        return {"id": "p1", "name": "João"}
    async def fake_get_link(pid, cid):
        return {"id": "pc1", "role": "agendamento"}
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "get_link", fake_get_link)
    r = client.get("/api/atendente/paciente/p1",
                   params={"contact_id": "c1", "token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["patient"]["id"] == "p1"
    assert body["link"]["id"] == "pc1"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -q`
Expected: FAIL (`ModuleNotFoundError: attendant_routes`).

- [ ] **Step 3: Implementar**

Criar `dashboard/attendant_routes.py`:

```python
import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, status

import attendant_db

router = APIRouter(prefix="/api/atendente")


def verify_token(token: str = Query(default="")) -> None:
    expected = os.getenv("ATTENDANT_PANEL_TOKEN", "")
    if not expected or not compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token inválido")


@router.get("/resolve")
async def resolve(phone: str, _: None = Depends(verify_token)):
    return await attendant_db.resolve_contact_and_patients(phone)


@router.get("/paciente/{patient_id}")
async def paciente(patient_id: str, contact_id: str, _: None = Depends(verify_token)):
    patient = await attendant_db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="paciente não encontrado")
    link = await attendant_db.get_link(patient_id, contact_id)
    return {"patient": patient, "link": link}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py
git commit -m "feat(dashboard): auth por token + rotas de leitura do painel"
```

---

## Task 9: Rotas de escrita (updates + reset)

**Files:**
- Modify: `dashboard/attendant_routes.py`
- Test: `dashboard/tests/test_attendant_routes.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `dashboard/tests/test_attendant_routes.py`:

```python
def test_update_patient_calls_db_and_logs(client, monkeypatch):
    calls = {}
    async def fake_update(pid, data):
        calls["update"] = (pid, data)
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "update_patient", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/paciente/p1",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888@s.whatsapp.net",
                          "data": {"name": "João Silva", "is_returning_patient": True}})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls["update"][0] == "p1"
    assert calls["update"][1]["name"] == "João Silva"
    assert calls["log"][0] == "attendant_edit_patient"


def test_update_patient_requires_token(client):
    r = client.post("/api/atendente/paciente/p1", json={"data": {}})
    assert r.status_code == 401


def test_reset_checkpoint_endpoint(client, monkeypatch):
    calls = {}
    async def fake_reset(phone):
        calls["reset"] = phone
        return 3
    async def fake_log(event_type, phone, metadata):
        calls["log"] = (event_type, phone, metadata)
    monkeypatch.setattr(attendant_db, "reset_checkpoint", fake_reset)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/reset-checkpoint",
                    params={"token": "test-token"},
                    json={"phone": "5581999998888@s.whatsapp.net"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": 3}
    assert calls["reset"] == "5581999998888@s.whatsapp.net"
    assert calls["log"][0] == "attendant_reset_checkpoint"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -q`
Expected: FAIL (404, rotas POST não existem).

- [ ] **Step 3: Implementar**

Acrescentar a `dashboard/attendant_routes.py` (importar `BaseModel`):

No topo, ajustar imports:

```python
from pydantic import BaseModel
```

E acrescentar os modelos + rotas:

```python
class UpdateBody(BaseModel):
    phone: str
    data: dict


class ResetBody(BaseModel):
    phone: str


@router.post("/contato/{contact_id}")
async def update_contato(contact_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await attendant_db.update_contact(contact_id, body.data)
    await attendant_db.log_event("attendant_edit_contact", body.phone,
                                 {"contact_id": contact_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/paciente/{patient_id}")
async def update_paciente(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await attendant_db.update_patient(patient_id, body.data)
    await attendant_db.log_event("attendant_edit_patient", body.phone,
                                 {"patient_id": patient_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/vinculo/{pc_id}")
async def update_vinculo(pc_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await attendant_db.update_link(pc_id, body.data)
    await attendant_db.log_event("attendant_edit_link", body.phone,
                                 {"pc_id": pc_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/reset-checkpoint")
async def reset_checkpoint(body: ResetBody, _: None = Depends(verify_token)):
    deleted = await attendant_db.reset_checkpoint(body.phone)
    await attendant_db.log_event("attendant_reset_checkpoint", body.phone, {"deleted": deleted})
    return {"ok": True, "deleted": deleted}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py
git commit -m "feat(dashboard): rotas de escrita + reset do checkpoint"
```

---

## Task 10: Montar o router e liberar iframe no app

**Files:**
- Modify: `dashboard/main.py`
- Test: `dashboard/tests/test_attendant_routes.py`

> O Chatwoot embute o painel num iframe. O `X-Frame-Options` default bloquearia; usamos `Content-Security-Policy: frame-ancestors` com o domínio do Chatwoot, configurável por env `CHATWOOT_FRAME_ANCESTOR` (default permite o próprio host para testes).

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `dashboard/tests/test_attendant_routes.py`:

```python
def test_main_app_includes_router_and_csp(monkeypatch):
    # importa o app real do dashboard e checa que a rota existe + header de frame
    import main as dashboard_main

    async def fake_resolve(phone):
        return {"contact": None, "patients": []}
    monkeypatch.setattr(attendant_db, "resolve_contact_and_patients", fake_resolve)

    c = TestClient(dashboard_main.app)
    r = c.get("/api/atendente/resolve",
              params={"phone": "5581999998888", "token": "test-token"})
    assert r.status_code == 200
    assert "frame-ancestors" in r.headers.get("content-security-policy", "")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py::test_main_app_includes_router_and_csp -q`
Expected: FAIL (rota 404 ou header ausente).

- [ ] **Step 3: Implementar**

Em `dashboard/main.py`:

(a) Após `app = FastAPI(title="Psique Dashboard", lifespan=lifespan)`, incluir o router e um middleware de CSP:

```python
import attendant_routes

app.include_router(attendant_routes.router)

_FRAME_ANCESTOR = os.getenv("CHATWOOT_FRAME_ANCESTOR", "'self'")


@app.middleware("http")
async def _frame_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors 'self' {_FRAME_ANCESTOR}"
    # remove X-Frame-Options se algum proxy o adicionar (CSP é a fonte da verdade)
    response.headers.pop("X-Frame-Options", None)
    return response
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS (todos).

- [ ] **Step 6: Commit**

```bash
git add dashboard/main.py dashboard/tests/test_attendant_routes.py
git commit -m "feat(dashboard): monta router do painel + libera embed em iframe"
```

---

## Task 11: Página da atendente (template + JS)

**Files:**
- Create: `dashboard/templates/atendente.html`
- Modify: `dashboard/main.py`

> Esta é a UI. HTML/JS não tem teste unitário aqui; a verificação é manual (checklist no fim). O JS lê o telefone via `postMessage` do Chatwoot ou via `?phone=` (teste fora do Chatwoot), busca os dados, renderiza seletor + formulários, e salva por seção.

- [ ] **Step 1: Rota da página**

Em `dashboard/main.py`, acrescentar:

```python
ATTENDANT_PANEL_TOKEN = os.getenv("ATTENDANT_PANEL_TOKEN", "")


@app.get("/atendente")
async def atendente_page(request: Request):
    # auth real é por token nas chamadas /api; a página em si só injeta o token no JS
    return templates.TemplateResponse(request, "atendente.html",
                                      {"token": ATTENDANT_PANEL_TOKEN})
```

- [ ] **Step 2: Template**

Criar `dashboard/templates/atendente.html`:

```html
{% extends "base.html" %}
{% block content %}
<style>body { overflow: auto !important; height: auto !important; }</style>
<div class="min-h-screen bg-gray-100 p-4">
  <div class="max-w-2xl mx-auto">
    <h1 class="text-lg font-semibold text-gray-800 mb-3">Painel da Atendente</h1>
    <div id="status" class="text-sm text-gray-500 mb-3">Carregando contato…</div>

    <div id="patient-selector" class="hidden mb-4">
      <label class="block text-xs uppercase text-gray-500 mb-1">Paciente</label>
      <select id="patient-select" class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"></select>
    </div>

    <div id="forms" class="hidden space-y-4"></div>

    <div id="reset-box" class="hidden mt-6 border-t pt-4">
      <button id="reset-btn"
        class="bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded">
        Resetar checkpoint da Eva
      </button>
      <p class="text-xs text-gray-400 mt-1">
        Apaga a memória da conversa. Na próxima mensagem, a Eva relê os dados corrigidos do banco.
      </p>
    </div>
  </div>
</div>

<script>
const TOKEN = {{ token | tojson }};
let PHONE = null;
let CONTACT = null;

// ── 1. Obter telefone: Chatwoot postMessage ou ?phone= (teste) ──────────────
function initPhone() {
  const qp = new URLSearchParams(location.search).get("phone");
  if (qp) { PHONE = qp; load(); return; }
  window.addEventListener("message", (e) => {
    try {
      const data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      if (data && data.event === "appContext" && data.data && data.data.contact) {
        PHONE = data.data.contact.phone_number || PHONE;
        if (PHONE) load();
      }
    } catch (_) {}
  });
  window.parent.postMessage("chatwoot-dashboard-app:fetch-info", "*");
}

function setStatus(t) { document.getElementById("status").textContent = t; }

// ── 2. Resolver contato + pacientes ─────────────────────────────────────────
async function load() {
  setStatus("Buscando paciente…");
  const r = await fetch(`/api/atendente/resolve?phone=${encodeURIComponent(PHONE)}&token=${encodeURIComponent(TOKEN)}`);
  if (!r.ok) { setStatus("Erro ao buscar (token?)."); return; }
  const { contact, patients } = await r.json();
  CONTACT = contact;
  if (!contact) { setStatus("Nenhum contato encontrado para este número."); return; }
  setStatus(`Contato: ${contact.name || contact.phone}`);
  document.getElementById("reset-box").classList.remove("hidden");

  const sel = document.getElementById("patient-select");
  sel.innerHTML = "";
  if (!patients.length) {
    document.getElementById("forms").classList.remove("hidden");
    renderForms(null);
    return;
  }
  patients.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id; o.textContent = p.name; sel.appendChild(o);
  });
  document.getElementById("patient-selector").classList.toggle("hidden", patients.length <= 1);
  sel.onchange = () => loadPatient(sel.value);
  loadPatient(patients[0].id);
}

// ── 3. Carregar paciente + vínculo ──────────────────────────────────────────
async function loadPatient(pid) {
  const r = await fetch(`/api/atendente/paciente/${pid}?contact_id=${CONTACT.id}&token=${encodeURIComponent(TOKEN)}`);
  if (!r.ok) { setStatus("Erro ao carregar paciente."); return; }
  const { patient, link } = await r.json();
  document.getElementById("forms").classList.remove("hidden");
  renderForms(patient, link);
}

// ── 4. Renderizar formulários ───────────────────────────────────────────────
function field(label, id, value, type = "text") {
  const v = value == null ? "" : value;
  return `<div class="mb-2">
    <label class="block text-xs uppercase text-gray-500 mb-0.5">${label}</label>
    <input id="${id}" type="${type}" value="${String(v).replace(/"/g, "&quot;")}"
      class="w-full border border-gray-300 rounded px-2 py-1 text-sm">
  </div>`;
}
function checkbox(label, id, checked) {
  return `<label class="flex items-center gap-2 text-sm mb-2">
    <input id="${id}" type="checkbox" ${checked ? "checked" : ""}> ${label}
  </label>`;
}
function select(label, id, value, options) {
  const opts = options.map(([v, t]) =>
    `<option value="${v}" ${String(value) === String(v) ? "selected" : ""}>${t}</option>`).join("");
  return `<div class="mb-2"><label class="block text-xs uppercase text-gray-500 mb-0.5">${label}</label>
    <select id="${id}" class="w-full border border-gray-300 rounded px-2 py-1 text-sm">${opts}</select></div>`;
}

const DOCTORS = [["", "—"],
  ["d5baa58b-a788-4f40-b8c0-512c189150be", "Dr. Júlio"],
  ["18b01f87-eacd-4905-bd4a-a8293991e6fd", "Dra. Bruna"]];

function renderForms(patient, link) {
  const c = CONTACT;
  let html = `<section class="bg-white rounded-lg shadow p-4">
    <h2 class="font-medium text-gray-700 mb-2">Contato</h2>
    ${field("Nome", "c_name", c.name)}
    ${field("CPF", "c_cpf", c.cpf)}
    ${field("Telefone", "c_phone", c.phone)}
    ${checkbox("Ativo", "c_active", c.active)}
    ${checkbox("Manual hold (silenciar Eva)", "c_manual_hold", c.manual_hold)}
    <button onclick="saveContact()" class="mt-2 bg-wa-green hover:bg-wa-green-dk text-white text-xs px-3 py-1.5 rounded">Salvar contato</button>
  </section>`;

  if (patient) {
    html += `<section class="bg-white rounded-lg shadow p-4" data-pid="${patient.id}" data-pcid="${link ? link.id : ""}">
      <h2 class="font-medium text-gray-700 mb-2">Paciente</h2>
      ${field("Nome", "p_name", patient.name)}
      ${field("Nascimento (dd/mm/aaaa)", "p_birth", patient.birth_date)}
      ${field("CPF", "p_cpf", patient.patient_cpf)}
      ${field("E-mail", "p_email", patient.email)}
      ${select("Médico", "p_doctor", patient.doctor_id || "", DOCTORS)}
      ${checkbox("Paciente retornante", "p_returning", patient.is_returning_patient)}
      ${select("Modalidade", "p_modality", patient.modality_restriction || "", [["", "—"], ["online", "Online"], ["presencial", "Presencial"]])}
      ${checkbox("Exceção de idade", "p_age_exc", patient.age_exception)}
      ${field("Preço custom (R$)", "p_price", patient.custom_price, "number")}
      ${field("Financeiro — nome", "p_fin_name", patient.financial_name)}
      ${field("Financeiro — CPF", "p_fin_cpf", patient.financial_cpf)}
      ${field("Financeiro — e-mail", "p_fin_email", patient.financial_email)}
      <button onclick="savePatient('${patient.id}')" class="mt-2 bg-wa-green hover:bg-wa-green-dk text-white text-xs px-3 py-1.5 rounded">Salvar paciente</button>`;
    if (link) {
      html += `<hr class="my-3">
        ${select("Papel do contato", "l_role", link.role, [["agendamento", "Agendamento"], ["financeiro", "Financeiro"], ["consulta", "Consulta"]])}
        ${checkbox("É o próprio paciente (is_self)", "l_is_self", link.is_self)}
        ${field("Relação (mãe/pai/tutor)", "l_rel", link.relationship)}
        <button onclick="saveLink('${link.id}')" class="mt-2 bg-gray-600 hover:bg-gray-700 text-white text-xs px-3 py-1.5 rounded">Salvar vínculo</button>`;
    }
    html += `</section>`;
  }
  document.getElementById("forms").innerHTML = html;
}

// ── 5. Salvar ────────────────────────────────────────────────────────────────
async function post(url, body) {
  const r = await fetch(`${url}?token=${encodeURIComponent(TOKEN)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone: PHONE, ...body }),
  });
  if (!r.ok) { alert("Erro ao salvar."); return false; }
  return true;
}
const val = (id) => document.getElementById(id).value;
const chk = (id) => document.getElementById(id).checked;
const numOrNull = (id) => { const v = val(id); return v === "" ? null : parseInt(v, 10); };

async function saveContact() {
  const ok = await post(`/api/atendente/contato/${CONTACT.id}`, { data: {
    name: val("c_name"), cpf: val("c_cpf"), phone: val("c_phone"),
    active: chk("c_active"), manual_hold: chk("c_manual_hold"),
  }});
  if (ok) flash("Contato salvo ✓");
}
async function savePatient(pid) {
  const ok = await post(`/api/atendente/paciente/${pid}`, { data: {
    name: val("p_name"), birth_date: val("p_birth"), patient_cpf: val("p_cpf"),
    email: val("p_email"), doctor_id: val("p_doctor") || null,
    is_returning_patient: chk("p_returning"),
    modality_restriction: val("p_modality") || null,
    age_exception: chk("p_age_exc"), custom_price: numOrNull("p_price"),
    financial_name: val("p_fin_name"), financial_cpf: val("p_fin_cpf"),
    financial_email: val("p_fin_email"),
  }});
  if (ok) flash("Paciente salvo ✓");
}
async function saveLink(pcid) {
  const ok = await post(`/api/atendente/vinculo/${pcid}`, { data: {
    role: val("l_role"), is_self: chk("l_is_self"), relationship: val("l_rel"),
  }});
  if (ok) flash("Vínculo salvo ✓");
}

function flash(msg) { setStatus(msg); setTimeout(() => setStatus(CONTACT ? `Contato: ${CONTACT.name || CONTACT.phone}` : ""), 1500); }

// ── 6. Reset ─────────────────────────────────────────────────────────────────
document.getElementById("reset-btn").onclick = async () => {
  if (!confirm("Resetar o checkpoint da Eva para este número? A conversa será esquecida (mensagens permanecem).")) return;
  const r = await fetch(`/api/atendente/reset-checkpoint?token=${encodeURIComponent(TOKEN)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone: PHONE }),
  });
  if (!r.ok) { alert("Erro ao resetar."); return; }
  const { deleted } = await r.json();
  flash(`Checkpoint resetado (${deleted} linhas) ✓`);
};

initPhone();
</script>
{% endblock %}
```

- [ ] **Step 3: Verificação manual (local, fora do Chatwoot)**

Rodar o dashboard: a partir de `dashboard/`, `ATTENDANT_PANEL_TOKEN=test-token uv run uvicorn main:app --port 8001` (com `.env` apontando para o Supabase de teste/dev).

Abrir `http://localhost:8001/atendente?phone=<telefone_real_de_teste>`. Confirmar:
- [ ] Carrega o contato e (se houver) o seletor de pacientes.
- [ ] Formulários vêm preenchidos com os dados do banco.
- [ ] "Salvar paciente" persiste (recarregar a página mostra o novo valor).
- [ ] "Resetar checkpoint" pede confirmação e retorna a contagem de linhas.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/atendente.html dashboard/main.py
git commit -m "feat(dashboard): página da atendente (formulários + reset)"
```

---

## Task 12: Documentar env + Dashboard App do Chatwoot

**Files:**
- Modify: `.env.example`
- Modify: `dashboard/main.py` (nada de código — só garantir que as envs estão documentadas)

- [ ] **Step 1: Documentar envs**

Em `.env.example`, na seção do dashboard (ou ao final), acrescentar:

```bash
# Painel da atendente (embutido no Chatwoot)
ATTENDANT_PANEL_TOKEN=troque-por-um-segredo-aleatorio
# Domínio do Chatwoot que pode embutir o painel em iframe (frame-ancestors do CSP)
CHATWOOT_FRAME_ANCESTOR=https://seu-chatwoot.exemplo.host
```

- [ ] **Step 2: Anotar o passo de configuração no Chatwoot**

Acrescentar ao topo de `dashboard/templates/atendente.html` um comentário Jinja com o passo manual (não-renderizado):

```html
{# Configuração no Chatwoot: Settings → Integrations → Dashboard Apps → New.
   Endpoint URL: https://<dashboard>/atendente?token=<ATTENDANT_PANEL_TOKEN>
   O Chatwoot envia o contato da conversa via postMessage (appContext). #}
```

- [ ] **Step 3: Rodar a suíte completa**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS (todos).

- [ ] **Step 4: Commit**

```bash
git add .env.example dashboard/templates/atendente.html
git commit -m "docs(dashboard): env do painel + setup do Dashboard App no Chatwoot"
```

---

## Verificação final

- [ ] `cd dashboard && uv run pytest -q` → todos verdes.
- [ ] Painel abre via `?phone=` e carrega dados reais (dev).
- [ ] Edição persiste no banco.
- [ ] Reset retorna contagem e a Eva, na próxima mensagem, relê os dados corrigidos (testar com um número de teste).
- [ ] Embutido numa conversa real do Chatwoot, o telefone é detectado via `postMessage`.

## Notas para as próximas fases (fora deste plano)

- **Fase 2 (agendamentos):** exigirá tornar `app/google_calendar.py` disponível à imagem do dashboard (mudar contexto/Dockerfile do build, vendorizar, ou endpoint no bot). Cancelar/remarcar usa `appointment_id` como `event_id` do Calendar.
- **Fase 3:** criação de agendamento com `get_available_slots` + `create_event`, espelhando `confirm_appointment`.
