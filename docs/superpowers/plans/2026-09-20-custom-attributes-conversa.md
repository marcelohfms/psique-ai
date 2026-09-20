# Custom attributes do paciente na conversa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expor médico, próxima consulta, taxa de reserva e se é retornante como custom attributes de contato na lateral da conversa do Chatwoot, atualizados por cron, sem tocar o webhook nem o grafo.

**Architecture:** Um módulo puro (`app/patient_attributes.py`) monta os quatro valores a partir dos fatos do paciente da consulta mais próxima. Um cron (`scripts/sync_contact_attributes.py`) enumera os contatos com consulta futura (mais os já sincronizados), monta os valores reusando `get_users_by_phone`/`get_upcoming_appointments`, e só grava no Chatwoot quando algum valor muda (memória via evento). `app/chatwoot.py` ganha `set_contact_custom_attributes` e `find_or_create_contact_id`.

**Tech Stack:** Python, asyncio, Supabase (postgrest async), Chatwoot REST, pytest + pytest-asyncio.

**Referências de leitura:**
- `app/chatwoot.py` — `_request` (retry), `_headers`, `_search_contact`, `_create_contact`, `_strip_phone`, `_base_url`, `_account_id`.
- `app/database.py` — `get_users_by_phone` (:96), `get_user_by_phone` (:134), `get_upcoming_appointments` (:428), `log_event` (:296), `get_events_by_type` (:309), `DOCTOR_NAMES` (:40).
- `app/scheduling_stall.py` — `parse_ts`.
- `scripts/send_lead_stall_nudges.py` — padrão do cron irmão (estrutura de `main`, memória via evento, só-escreve-quando-muda).
- `tests/test_chatwoot.py` — padrão de mock de httpx.
- `tests/test_lead_stall.py` / `tests/test_lead_stall_crons.py` — padrão de teste puro e de cron.

**Convenções:** rodar tudo de dentro da worktree `.worktrees/custom-attributes`. Testes com `uv run pytest --tb=short`. Commits pequenos por tarefa. Cada commit termina com `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create** `app/patient_attributes.py` — lógica pura: constantes das `attribute_key`, o evento de memória, `pick_next_appointment`, `fee_status`, `format_next_appointment`, `build_attributes`, `needs_attr_change`.
- **Create** `scripts/sync_contact_attributes.py` — cron: enumeração de candidatos, montagem dos valores por telefone, reconciliação (só grava quando muda).
- **Create** `.github/workflows/sync_contact_attributes.yml` — agenda a cada 30 min.
- **Modify** `app/chatwoot.py` — `set_contact_custom_attributes` + `find_or_create_contact_id`.
- **Create** `tests/test_patient_attributes.py` — lógica pura.
- **Create** `tests/test_sync_contact_attributes.py` — cron com mocks.
- **Modify** `tests/test_chatwoot.py` — testes das duas funções novas.

---

## Task 1: Núcleo puro em `app/patient_attributes.py`

**Files:**
- Create: `app/patient_attributes.py`
- Test: `tests/test_patient_attributes.py`

- [ ] **Step 1: Escrever `tests/test_patient_attributes.py` com este conteúdo exato:**

```python
"""Testa a lógica pura dos custom attributes do paciente (app/patient_attributes.py)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.patient_attributes import (
    pick_next_appointment, fee_status, format_next_appointment,
    build_attributes, needs_attr_change,
    ATTR_EVENT, ATTR_KEYS,
)

TZ = ZoneInfo("America/Recife")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _appt(start, **over):
    a = {"status": "scheduled", "start_time": start.isoformat(),
         "modality": "online", "patient_id": "p1",
         "booking_fee_paid_at": None, "booking_fee_waived": False}
    a.update(over)
    return a


# ── pick_next_appointment ─────────────────────────────────────────────────────

def test_pick_next_returns_soonest_future_scheduled():
    a1 = _appt(NOW + timedelta(days=2))
    a2 = _appt(NOW + timedelta(days=1))
    a3 = _appt(NOW - timedelta(days=1))          # passada
    a4 = _appt(NOW + timedelta(days=3), status="completed")  # não scheduled
    got = pick_next_appointment([a1, a2, a3, a4], NOW)
    assert got is a2


def test_pick_next_none_when_no_future():
    a = _appt(NOW - timedelta(hours=1))
    assert pick_next_appointment([a], NOW) is None


# ── fee_status ────────────────────────────────────────────────────────────────

def test_fee_status_waived():
    assert fee_status(_appt(NOW, booking_fee_waived=True), None) == "Isenta"


def test_fee_status_courtesy_price_zero():
    assert fee_status(_appt(NOW), 0) == "Isenta"


def test_fee_status_paid():
    assert fee_status(_appt(NOW, booking_fee_paid_at="2026-09-19T10:00:00Z"), 200) == "Paga"


def test_fee_status_pending():
    assert fee_status(_appt(NOW), 200) == "Pendente"


# ── format_next_appointment ───────────────────────────────────────────────────

def test_format_next_appointment_has_date_time_modality_name():
    # 14:00 UTC = 11:00 em Recife (UTC-3)
    appt = _appt(datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc), modality="presencial")
    got = format_next_appointment(appt, "João Silva", TZ)
    assert got == "22/09/2026 14:00 presencial — João"


# ── build_attributes ──────────────────────────────────────────────────────────

def test_build_attributes_with_next_appointment():
    appt = _appt(datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc))
    attrs = build_attributes(
        doctor_label="Dr. Júlio", next_appt=appt, patient_name="João Silva",
        is_returning=True, custom_price=200, tz=TZ,
    )
    assert attrs == {
        "medico": "Dr. Júlio",
        "proxima_consulta": "22/09/2026 14:00 online — João",
        "taxa_reserva": "Pendente",
        "retornante": "Retornante",
    }
    assert set(attrs) == set(ATTR_KEYS)


def test_build_attributes_without_next_appointment():
    attrs = build_attributes(
        doctor_label="Dra. Bruna", next_appt=None, patient_name="Ana",
        is_returning=False, custom_price=None, tz=TZ,
    )
    assert attrs["proxima_consulta"] == "sem consulta futura"
    assert attrs["taxa_reserva"] == ""
    assert attrs["medico"] == "Dra. Bruna"
    assert attrs["retornante"] == "Primeira vez"


# ── needs_attr_change ─────────────────────────────────────────────────────────

def test_needs_attr_change():
    a = {"medico": "Dr. Júlio"}
    assert needs_attr_change(a, None) is True
    assert needs_attr_change(a, {"medico": "Dr. Júlio"}) is False
    assert needs_attr_change(a, {"medico": "Dra. Bruna"}) is True


def test_attr_event_name():
    assert ATTR_EVENT == "contact_attributes_synced"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patient_attributes.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.patient_attributes'`.

- [ ] **Step 3: Criar `app/patient_attributes.py` com este conteúdo exato:**

```python
"""Custom attributes do paciente para a lateral da conversa no Chatwoot.

Lógica pura (sem I/O). Os valores descrevem sempre o paciente da consulta mais
próxima do telefone. Gravados como custom attributes de CONTATO por um cron
(scripts/sync_contact_attributes.py). As attribute_key abaixo precisam existir
no painel do Chatwoot (Administração > Atributos personalizados, tipo contato).
"""
from datetime import datetime

from app.scheduling_stall import parse_ts

# attribute_key (usadas na API do Chatwoot e no cadastro do painel).
ATTR_MEDICO = "medico"
ATTR_PROXIMA = "proxima_consulta"
ATTR_TAXA = "taxa_reserva"
ATTR_RETORNANTE = "retornante"
ATTR_KEYS = (ATTR_MEDICO, ATTR_PROXIMA, ATTR_TAXA, ATTR_RETORNANTE)

# Evento phone-keyed (tabela events) que guarda o último dict gravado, para o
# cron só chamar o Chatwoot quando algum valor muda.
ATTR_EVENT = "contact_attributes_synced"


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


def pick_next_appointment(appts: list[dict], now: datetime) -> dict | None:
    """A consulta agendada futura mais próxima, ou None. Ignora não-scheduled e
    passadas."""
    future = [
        a for a in appts
        if a.get("status") == "scheduled" and parse_ts(a["start_time"]) >= now
    ]
    if not future:
        return None
    return min(future, key=lambda a: parse_ts(a["start_time"]))


def fee_status(next_appt: dict, custom_price) -> str:
    """Paga / Pendente / Isenta para a taxa de reserva da próxima consulta."""
    if next_appt.get("booking_fee_waived") or custom_price == 0:
        return "Isenta"
    if next_appt.get("booking_fee_paid_at"):
        return "Paga"
    return "Pendente"


def format_next_appointment(next_appt: dict, patient_name: str, tz) -> str:
    """'DD/MM/AAAA HH:MM <modalidade> — <primeiro nome>'."""
    dt = parse_ts(next_appt["start_time"]).astimezone(tz)
    quando = dt.strftime("%d/%m/%Y %H:%M")
    modalidade = next_appt.get("modality") or ""
    label = f"{quando} {modalidade}".strip()
    first = _first_name(patient_name)
    return f"{label} — {first}" if first else label


def build_attributes(*, doctor_label: str, next_appt: dict | None,
                     patient_name: str, is_returning, custom_price, tz) -> dict:
    """Monta o dict dos quatro custom attributes (todos as ATTR_KEYS)."""
    if next_appt:
        proxima = format_next_appointment(next_appt, patient_name, tz)
        taxa = fee_status(next_appt, custom_price)
    else:
        proxima = "sem consulta futura"
        taxa = ""
    return {
        ATTR_MEDICO: doctor_label or "",
        ATTR_PROXIMA: proxima,
        ATTR_TAXA: taxa,
        ATTR_RETORNANTE: "Retornante" if is_returning else "Primeira vez",
    }


def needs_attr_change(new: dict, last: dict | None) -> bool:
    """Só chama o Chatwoot quando algum valor mudou desde a última gravação."""
    return new != last
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patient_attributes.py -q`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add app/patient_attributes.py tests/test_patient_attributes.py
git commit -m "feat(custom-attributes): núcleo puro dos atributos do paciente

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Funções de contato em `app/chatwoot.py`

**Files:**
- Modify: `app/chatwoot.py`
- Test: `tests/test_chatwoot.py`

- [ ] **Step 1: Escrever os testes que falham. APÊNDICE ao final de `tests/test_chatwoot.py`:**

```python
# ── custom attributes de contato ──────────────────────────────────────────────

async def test_set_contact_custom_attributes_calls_api():
    from app.chatwoot import set_contact_custom_attributes
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch.dict("os.environ", {
            "CHATWOOT_BASE_URL": "https://chat.example.com",
            "CHATWOOT_ACCOUNT_ID": "1",
            "CHATWOOT_USER_TOKEN": "user-token",
        }):
            await set_contact_custom_attributes(7, {"medico": "Dr. Júlio"})

        mock_client.put.assert_called_once()
        url = mock_client.put.call_args[0][0]
        assert "/contacts/7" in url
        assert mock_client.put.call_args[1]["json"] == {"custom_attributes": {"medico": "Dr. Júlio"}}


async def test_find_or_create_contact_id_uses_existing():
    from app.chatwoot import find_or_create_contact_id
    with patch("app.chatwoot._search_contact", AsyncMock(return_value={"id": 55})):
        with patch.dict("os.environ", {
            "CHATWOOT_BASE_URL": "https://chat.example.com",
            "CHATWOOT_ACCOUNT_ID": "1",
            "CHATWOOT_USER_TOKEN": "user-token",
        }):
            got = await find_or_create_contact_id("5581999999999@s.whatsapp.net")
        assert got == 55


async def test_find_or_create_contact_id_creates_when_missing():
    from app.chatwoot import find_or_create_contact_id
    with patch("app.chatwoot._search_contact", AsyncMock(return_value=None)), \
         patch("app.chatwoot._create_contact", AsyncMock(return_value={"id": 88})):
        with patch.dict("os.environ", {
            "CHATWOOT_BASE_URL": "https://chat.example.com",
            "CHATWOOT_ACCOUNT_ID": "1",
            "CHATWOOT_USER_TOKEN": "user-token",
        }):
            got = await find_or_create_contact_id("5581999999999@s.whatsapp.net")
        assert got == 88
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_chatwoot.py -k "custom_attributes or contact_id" -q`
Expected: FAIL com ImportError (funções não existem).

- [ ] **Step 3: Implementar. Em `app/chatwoot.py`, adicionar estas duas funções (logo após `add_label`, por volta da linha 197):**

```python
async def set_contact_custom_attributes(contact_id: int, attrs: dict) -> None:
    """Grava custom attributes de CONTATO (aparecem na lateral da conversa).

    Envia o dict completo: a API do Chatwoot substitui o objeto custom_attributes
    inteiro, então quem chama deve mandar todas as chaves de uma vez. As chaves
    precisam existir como atributos de contato no painel para aparecerem."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/{contact_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        await _request(client, "PUT", url, json={"custom_attributes": attrs}, headers=_headers())


async def find_or_create_contact_id(phone: str) -> int | None:
    """Resolve o contact_id do Chatwoot por telefone, criando o contato se não
    existir. Reusa a busca/criação já usada por find_or_create_conversation."""
    digits = _strip_phone(phone)
    async with httpx.AsyncClient(timeout=10) as client:
        contact = await _search_contact(client, digits)
        if contact is None:
            contact = await _create_contact(client, digits)
        return contact.get("id")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_chatwoot.py -q`
Expected: PASS (todos, incluindo os novos).

- [ ] **Step 5: Commit**

```bash
git add app/chatwoot.py tests/test_chatwoot.py
git commit -m "feat(custom-attributes): grava custom attributes de contato no Chatwoot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> Nota: a API de update de contato do Chatwoot é `PUT /contacts/{id}`; a validação real de que os valores aparecem na lateral é feita no primeiro run manual (ver plano de teste), pois o teste aqui é mockado.

---

## Task 3: Cron `scripts/sync_contact_attributes.py`

**Files:**
- Create: `scripts/sync_contact_attributes.py`
- Test: `tests/test_sync_contact_attributes.py`

- [ ] **Step 1: Escrever `tests/test_sync_contact_attributes.py` com este conteúdo exato:**

```python
"""Testa o cron de custom attributes de contato (scripts/sync_contact_attributes.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import scripts.sync_contact_attributes as cron

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── candidate_phones (client fake por tabela) ─────────────────────────────────

class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *a, **k):
        return self
    def eq(self, *a, **k):
        return self
    def gte(self, *a, **k):
        return self
    def in_(self, *a, **k):
        return self
    async def execute(self):
        return MagicMock(data=self._rows)


class _FakeClient:
    def __init__(self, by_table):
        self._by_table = by_table
    def from_(self, table):
        return _FakeQuery(self._by_table.get(table, []))


async def test_candidate_phones_unions_appointments_and_synced():
    client = _FakeClient({
        "appointments": [{"patient_id": "p1"}, {"patient_id": "p1"}],
        "patient_contacts": [{"contact_id": "c1"}],
        "contacts": [{"phone": "5581111"}],
        "events": [{"phone": "5582222"}],   # já sincronizado antes
    })
    got = await cron.candidate_phones(client, NOW)
    assert got == {"5581111", "5582222"}


# ── _sync_one (reconciliação) ─────────────────────────────────────────────────

async def test_sync_one_writes_when_changed(monkeypatch):
    calls = {}

    async def fake_build(phone, now):
        return {"medico": "Dr. Júlio", "proxima_consulta": "x", "taxa_reserva": "Paga", "retornante": "Retornante"}

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nunca gravado

    async def fake_find_contact(phone):
        return 7

    async def fake_set_attrs(contact_id, attrs):
        calls["contact_id"] = contact_id
        calls["attrs"] = attrs

    async def fake_log_event(event_type, phone, metadata=None):
        calls["logged"] = (event_type, metadata)

    monkeypatch.setattr(cron, "_build_attrs_for_phone", fake_build)
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "find_or_create_contact_id", fake_find_contact)
    monkeypatch.setattr(cron, "set_contact_custom_attributes", fake_set_attrs)
    monkeypatch.setattr(cron, "log_event", fake_log_event)

    await cron._sync_one("5581111", NOW)

    assert calls["contact_id"] == 7
    assert calls["attrs"]["medico"] == "Dr. Júlio"
    assert calls["logged"][0] == "contact_attributes_synced"
    assert calls["logged"][1]["attrs"]["taxa_reserva"] == "Paga"


async def test_sync_one_skips_when_unchanged(monkeypatch):
    same = {"medico": "Dr. Júlio", "proxima_consulta": "x", "taxa_reserva": "Paga", "retornante": "Retornante"}

    async def fake_build(phone, now):
        return dict(same)

    async def fake_get_events(phone, event_type, limit=50):
        return [{"metadata": {"attrs": same}}]

    set_mock = AsyncMock()
    find_mock = AsyncMock()
    monkeypatch.setattr(cron, "_build_attrs_for_phone", fake_build)
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "set_contact_custom_attributes", set_mock)
    monkeypatch.setattr(cron, "find_or_create_contact_id", find_mock)

    await cron._sync_one("5581111", NOW)

    set_mock.assert_not_awaited()
    find_mock.assert_not_awaited()


async def test_sync_one_does_not_log_when_api_fails(monkeypatch):
    async def fake_build(phone, now):
        return {"medico": "Dr. Júlio", "proxima_consulta": "x", "taxa_reserva": "Paga", "retornante": "Retornante"}

    async def fake_get_events(phone, event_type, limit=50):
        return []

    async def fake_find_contact(phone):
        return 7

    async def fake_set_attrs(contact_id, attrs):
        raise RuntimeError("chatwoot down")

    log_mock = AsyncMock()
    monkeypatch.setattr(cron, "_build_attrs_for_phone", fake_build)
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "find_or_create_contact_id", fake_find_contact)
    monkeypatch.setattr(cron, "set_contact_custom_attributes", fake_set_attrs)
    monkeypatch.setattr(cron, "log_event", log_mock)

    await cron._sync_one("5581111", NOW)

    log_mock.assert_not_awaited()  # falha não grava a memória → próximo run tenta de novo


# ── _build_attrs_for_phone (fatos mockados) ───────────────────────────────────

async def test_build_attrs_for_phone_uses_next_appointment_patient(monkeypatch):
    async def fake_users(phone):
        return [
            {"id": "p1", "name": "Ana", "doctor_id": cron.DOCTOR_IDS_BY_KEY["julio"],
             "is_returning_patient": True, "custom_price": 200},
            {"id": "p2", "name": "João", "doctor_id": cron.DOCTOR_IDS_BY_KEY["bruna"],
             "is_returning_patient": False, "custom_price": 200},
        ]

    async def fake_upcoming(phone):
        return [{"status": "scheduled", "start_time": (NOW + timedelta(days=1)).isoformat(),
                 "modality": "online", "patient_id": "p2",
                 "booking_fee_paid_at": None, "booking_fee_waived": False}]

    async def fake_get_user(phone):
        return {"id": "p1"}

    monkeypatch.setattr(cron, "get_users_by_phone", fake_users)
    monkeypatch.setattr(cron, "get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)

    attrs = await cron._build_attrs_for_phone("5581111", NOW)
    assert attrs["medico"] == "Dra. Bruna"          # paciente da consulta mais próxima (p2)
    assert "João" in attrs["proxima_consulta"]
    assert attrs["retornante"] == "Primeira vez"
    assert attrs["taxa_reserva"] == "Pendente"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_sync_contact_attributes.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.sync_contact_attributes'`.

- [ ] **Step 3: Criar `scripts/sync_contact_attributes.py` com este conteúdo exato:**

```python
"""Sincroniza custom attributes de contato no Chatwoot (médico, próxima consulta,
taxa de reserva, retornante). Roda a cada 30 min via GitHub Actions.

- Candidatos: contatos com consulta futura agendada + os já sincronizados antes.
- Só chama o Chatwoot quando algum valor muda (memória via evento
  contact_attributes_synced) — protege do rate limit.
- Grava mesmo para contato pausado: custom attribute é info para a atendente, não
  mensagem ao paciente. Nada toca labels de controle nem o webhook/grafo.
"""
import asyncio
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.patient_attributes import (
    build_attributes, needs_attr_change, pick_next_appointment, ATTR_EVENT,
)
from app.database import (
    get_users_by_phone, get_user_by_phone, get_upcoming_appointments,
    log_event, get_events_by_type, DOCTOR_NAMES, DOCTOR_IDS,
)
from app.chatwoot import set_contact_custom_attributes, find_or_create_contact_id

TZ = ZoneInfo("America/Recife")

# Reexport para os testes referenciarem os ids reais por chave.
DOCTOR_IDS_BY_KEY = DOCTOR_IDS

DOCTOR_LABELS = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}


def _doctor_label(doctor_id) -> str:
    return DOCTOR_LABELS.get(DOCTOR_NAMES.get(doctor_id, ""), "")


async def candidate_phones(client, now: datetime) -> set[str]:
    """Telefones a sincronizar: quem tem consulta futura agendada + quem já foi
    sincronizado antes (para atualizar quando a consulta passa/cancela)."""
    now_iso = now.astimezone(timezone.utc).isoformat()
    appts = (
        await client.from_("appointments")
        .select("patient_id")
        .eq("status", "scheduled")
        .gte("start_time", now_iso)
        .execute()
    ).data or []
    patient_ids = list({a["patient_id"] for a in appts if a.get("patient_id")})

    phones: set[str] = set()
    if patient_ids:
        pcs = (
            await client.from_("patient_contacts")
            .select("contact_id")
            .in_("patient_id", patient_ids)
            .execute()
        ).data or []
        contact_ids = list({pc["contact_id"] for pc in pcs if pc.get("contact_id")})
        if contact_ids:
            contacts = (
                await client.from_("contacts")
                .select("phone")
                .in_("id", contact_ids)
                .execute()
            ).data or []
            phones = {c["phone"] for c in contacts if c.get("phone")}

    synced = (
        await client.from_("events")
        .select("phone")
        .eq("event_type", ATTR_EVENT)
        .execute()
    ).data or []
    phones |= {e["phone"] for e in synced if e.get("phone")}
    return phones


async def _build_attrs_for_phone(phone: str, now: datetime) -> dict:
    """Monta os quatro valores para o telefone, a partir do paciente da consulta
    mais próxima (ou do paciente mais recente quando não há consulta futura)."""
    users = await get_users_by_phone(phone) or []
    by_id = {u["id"]: u for u in users}
    appts = await get_upcoming_appointments(phone)
    next_appt = pick_next_appointment(appts, now)

    if next_appt and next_appt.get("patient_id") in by_id:
        user = by_id[next_appt["patient_id"]]
    else:
        user = await get_user_by_phone(phone) or (users[0] if users else {})

    patient_name = user.get("patient_name") or user.get("name") or ""
    return build_attributes(
        doctor_label=_doctor_label(user.get("doctor_id")),
        next_appt=next_appt,
        patient_name=patient_name,
        is_returning=user.get("is_returning_patient"),
        custom_price=user.get("custom_price"),
        tz=TZ,
    )


async def _sync_one(phone: str, now: datetime) -> None:
    """Reconcilia os custom attributes de um contato: só grava quando muda."""
    attrs = await _build_attrs_for_phone(phone, now)

    events = await get_events_by_type(phone, ATTR_EVENT, limit=1)
    last = (events[0].get("metadata") or {}).get("attrs") if events else None
    if not needs_attr_change(attrs, last):
        return

    contact_id = await find_or_create_contact_id(phone)
    if contact_id is None:
        print(f"  [attrs] sem contact_id no Chatwoot para {phone}")
        return

    try:
        await set_contact_custom_attributes(contact_id, attrs)
    except Exception as e:
        print(f"  [attrs] set falhou para {phone}: {type(e).__name__}: {e}")
        return  # não grava a memória — próximo run tenta de novo

    await log_event(ATTR_EVENT, phone, {"attrs": attrs, "contact_id": contact_id})
    print(f"  [attrs] atualizado {phone}: {attrs}")


async def main() -> None:
    from supabase import acreate_client

    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    phones = await candidate_phones(client, now)
    print(f"Contatos a sincronizar: {len(phones)}")
    for phone in phones:
        try:
            await _sync_one(phone, now)
        except Exception as e:
            print(f"  [attrs] erro inesperado em {phone}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_sync_contact_attributes.py -q`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_contact_attributes.py tests/test_sync_contact_attributes.py
git commit -m "feat(custom-attributes): cron que sincroniza atributos do paciente no contato

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Workflow do GitHub Actions

**Files:**
- Create: `.github/workflows/sync_contact_attributes.yml`

- [ ] **Step 1: Ler o workflow irmão** `.github/workflows/scheduling_stall_nudges.yml` para copiar a estrutura exata (checkout, setup-uv, uv sync, bloco `env:`, comando).

- [ ] **Step 2: Criar `.github/workflows/sync_contact_attributes.yml`** com o mesmo esqueleto, mudando só:
  - `name:` → `Sync contact attributes`
  - `schedule: - cron:` → `"*/30 * * * *"`
  - manter `workflow_dispatch:` (run manual)
  - o comando final → `uv run python scripts/sync_contact_attributes.py`
  - o bloco `env:` deve conter as MESMAS variáveis do irmão que este script usa: `SUPABASE_URL`, `SUPABASE_KEY`, e as `CHATWOOT_*` (BASE_URL, ACCOUNT_ID, USER_TOKEN, AGENT_BOT_TOKEN, INBOX_ID). Não precisa de SMTP nem de `SUPABASE_CONNECTION_STRING` (não há checkpoint aqui).

- [ ] **Step 3: Validar YAML**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/sync_contact_attributes.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 4: Conferir que as CHATWOOT_/SUPABASE_ envs presentes batem com o irmão**

Run: `for v in SUPABASE_URL SUPABASE_KEY CHATWOOT_BASE_URL CHATWOOT_ACCOUNT_ID CHATWOOT_USER_TOKEN CHATWOOT_AGENT_BOT_TOKEN CHATWOOT_INBOX_ID; do grep -q "$v" .github/workflows/sync_contact_attributes.yml && echo "ok $v" || echo "FALTA $v"; done`
Expected: `ok` para cada uma. Ajustar se faltar.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/sync_contact_attributes.yml
git commit -m "ci(custom-attributes): workflow do cron a cada 30 min

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Suíte completa + verificação

**Files:** nenhum.

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS (sem regressão).

- [ ] **Step 2: Smoke import**

Run: `uv run python -c "import scripts.sync_contact_attributes as c; import app.patient_attributes as m; from app.chatwoot import set_contact_custom_attributes, find_or_create_contact_id; print('ok', bool(c.main), bool(m.build_attributes))"`
Expected: `ok True True`

- [ ] **Step 3: Commit final se necessário**

```bash
git add -A && git commit -m "test(custom-attributes): garante suíte verde" || echo "nada a commitar"
```

---

## Notas de operação (handoff, não são passos de código)

- Antes de confiar no automático: criar no painel do Chatwoot (Administração > Atributos personalizados) os quatro atributos **de contato** com as `attribute_key` exatas: `medico`, `proxima_consulta`, `taxa_reserva`, `retornante` (tipo texto). Depois rodar o workflow 1x manual (`workflow_dispatch`) e conferir que a lateral de um contato com consulta futura mostra os valores.
- Se os valores não aparecerem na lateral mas o log disser que gravou, quase sempre é a `attribute_key` não cadastrada no painel (ou tipo/modelo errado: precisa ser atributo de CONTATO).
- Depois de mergeado: `git worktree remove .worktrees/custom-attributes`.

---

## Self-review (autor do plano)

- **Cobertura do spec:** quatro atributos e valores (Task 1); paciente da consulta mais próxima + nome (Task 1 `pick_next_appointment`/`format_next_appointment`, Task 3 `_build_attrs_for_phone`); gravação no contato (Task 2); cron 30 min + candidatos (consulta futura ∪ já sincronizados) (Task 3/4); só-escreve-quando-muda + memória por evento (Task 1/3); grava mesmo pausado (Task 3 não checa pausa — deliberado, conforme spec); passo operacional dos 4 campos (notas). Coberto.
- **Placeholders:** nenhum; todo passo de código tem o código real.
- **Consistência de nomes:** `build_attributes`, `needs_attr_change`, `pick_next_appointment`, `fee_status`, `format_next_appointment`, `ATTR_EVENT`, `ATTR_KEYS`, `set_contact_custom_attributes`, `find_or_create_contact_id`, `candidate_phones`, `_build_attrs_for_phone`, `_sync_one` usados iguais em todas as tarefas. O cron reexporta `DOCTOR_IDS_BY_KEY = DOCTOR_IDS` só para o teste referenciar ids por chave.
