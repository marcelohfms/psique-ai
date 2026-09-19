# Situação do lead (abandono no cadastro + labels) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rastrear o lead que abandona no meio do cadastro, cutucá-lo por WhatsApp e refletir a situação do lead (`lead-novo`, `cadastro-abandonado`, `agendamento-abandonado`) como label no Chatwoot, tudo na camada de cron sem tocar o webhook nem o grafo.

**Architecture:** Um módulo puro/testável novo (`app/lead_stall.py`) com a classificação da situação e a decisão de mudança de label; um cron novo (`scripts/send_lead_stall_nudges.py`) que roda a cada 30 min, reconcilia as labels e envia o nudge; e uma seção nova no relatório diário que a clínica já recebe (`scripts/send_scheduling_stall_report.py`). Reusa `app/scheduling_stall.py` (abandono de agendamento) e os helpers já batidos de envio/janela/checkpoint de `scripts/send_payment_reminders.py`.

**Tech Stack:** Python, asyncio, Supabase (postgrest async), Chatwoot REST (`app/chatwoot.py`), pytest + pytest-asyncio.

**Referências de leitura (não modificar sem necessidade):**
- `app/scheduling_stall.py` — padrão de detecção (`parse_ts`, `fetch_abandoned`, `is_nudge_eligible`, `mark_handled`).
- `scripts/send_scheduling_stall_nudges.py` — padrão do cron de nudge.
- `scripts/send_scheduling_stall_report.py` — o relatório que vamos estender.
- `scripts/send_payment_reminders.py` — `send_whatsapp` (linha 91), `save_to_checkpoint` (185), `_window_open` (230). Todos normalizam dígitos→JID internamente.
- `app/database.py` — `get_user_by_phone` (134), `is_registration_complete` (333), `get_upcoming_appointments` (428), `log_event` (296), `get_events_by_type` (309).
- `app/chatwoot.py` — `set_labels` (181), `find_or_create_conversation` (319), `get_conversation_id` (86).
- `tests/test_scheduling_stall.py` e `tests/test_scheduling_stall_crons.py` — padrão de teste (fake client, monkeypatch).

**Convenções:** rodar tudo de dentro da worktree `.worktrees/situacao-lead`. Testes com `uv run pytest --tb=short`. Commits pequenos por tarefa.

---

## File Structure

- **Create** `app/lead_stall.py` — constantes, lógica pura (`classify_situation`, `select_recent_phones`, `label_ops`, `needs_label_change`) e o orquestrador async `evaluate_leads`.
- **Create** `scripts/send_lead_stall_nudges.py` — cron: reconcilia labels + nudge de cadastro abandonado.
- **Create** `.github/workflows/lead_stall_nudges.yml` — agenda o cron a cada 30 min.
- **Create** `tests/test_lead_stall.py` — testa `app/lead_stall.py` (puro + orquestrador com client fake/monkeypatch).
- **Create** `tests/test_lead_stall_crons.py` — testa o cron (nudge + reconciliação) com mocks.
- **Modify** `scripts/send_scheduling_stall_report.py` — adicionar a seção de `cadastro-abandonado` frio ao e-mail diário.
- **Modify** `tests/test_scheduling_stall_crons.py` **ou** adicionar em `tests/test_lead_stall_crons.py` — cobrir a seção nova do relatório (usaremos `test_lead_stall_crons.py`).

---

## Task 1: Núcleo puro em `app/lead_stall.py`

**Files:**
- Create: `app/lead_stall.py`
- Test: `tests/test_lead_stall.py`

- [ ] **Step 1: Escrever os testes que falham (classificação + seleção + label ops)**

Criar `tests/test_lead_stall.py`:

```python
"""Testa a lógica pura de situação do lead (app/lead_stall.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.lead_stall import (
    classify_situation, select_recent_phones, label_ops, needs_label_change,
    LABEL_NEW, LABEL_CADASTRO, LABEL_AGENDAMENTO, LEAD_LABELS,
)

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


def _base(**over):
    facts = dict(
        active=True,
        has_appointment=False,
        offered_abandoned=False,
        registration_complete=False,
        has_name=True,
        last_msg_at=NOW - timedelta(minutes=30),
        now=NOW,
    )
    facts.update(over)
    return facts


# ── classify_situation ────────────────────────────────────────────────────────

def test_fresh_incomplete_registration_is_lead_novo():
    assert classify_situation(**_base()) == LABEL_NEW


def test_silent_with_name_is_cadastro_abandonado():
    assert classify_situation(**_base(last_msg_at=NOW - timedelta(hours=5))) == LABEL_CADASTRO


def test_silent_without_name_is_nothing():
    assert classify_situation(**_base(last_msg_at=NOW - timedelta(hours=5), has_name=False)) is None


def test_offered_abandoned_is_agendamento_abandonado():
    # cadastro completo + viu horários e não confirmou
    assert classify_situation(**_base(registration_complete=True, offered_abandoned=True)) == LABEL_AGENDAMENTO


def test_completed_registration_without_abandon_is_nothing():
    assert classify_situation(**_base(registration_complete=True)) is None


def test_has_appointment_is_nothing():
    assert classify_situation(**_base(has_appointment=True, offered_abandoned=True)) is None


def test_paused_is_nothing():
    assert classify_situation(**_base(active=False, last_msg_at=NOW - timedelta(hours=5))) is None


# ── select_recent_phones ──────────────────────────────────────────────────────

def _msg(phone, role, dt):
    return {"phone": phone, "role": role, "created_at": dt.isoformat()}


def test_select_keeps_latest_user_message_within_window():
    rows = [
        _msg("5581111", "user", NOW - timedelta(days=1)),
        _msg("5581111", "user", NOW - timedelta(hours=2)),
        _msg("5582222", "assistant", NOW - timedelta(hours=1)),  # não conta (assistant)
        _msg("5583333", "user", NOW - timedelta(days=30)),        # fora da janela
    ]
    got = select_recent_phones(rows, NOW)
    assert set(got) == {"5581111"}
    assert got["5581111"] == NOW - timedelta(hours=2)


# ── label_ops / needs_label_change ────────────────────────────────────────────

def test_label_ops_sets_one_removes_others():
    add, remove = label_ops(LABEL_CADASTRO)
    assert add == [LABEL_CADASTRO]
    assert set(remove) == set(LEAD_LABELS) - {LABEL_CADASTRO}


def test_label_ops_none_removes_all():
    add, remove = label_ops(None)
    assert add == []
    assert set(remove) == set(LEAD_LABELS)


def test_needs_label_change():
    assert needs_label_change(LABEL_NEW, None) is True
    assert needs_label_change(LABEL_NEW, LABEL_NEW) is False
    assert needs_label_change(None, LABEL_NEW) is True
    assert needs_label_change(None, None) is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_lead_stall.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.lead_stall'`.

- [ ] **Step 3: Implementar o núcleo puro**

Criar `app/lead_stall.py`:

```python
"""Situação do lead: classificação pura + labels do funil de lead.

Roda só na camada de cron (nenhum uso no webhook/grafo). A detecção lê tabelas
que já existem (messages, events, patients/contacts, appointments) e reusa
app/scheduling_stall.py para a parte de agendamento abandonado.

Labels (mutuamente exclusivas, no máximo uma por lead):
  - lead-novo             → começou, cadastro incompleto, ainda dentro do prazo
  - cadastro-abandonado   → deu o nome, cadastro incompleto, 4h+ de silêncio
  - agendamento-abandonado→ viu horários e não confirmou (via scheduling_stall)
"""
from datetime import datetime, timedelta

from app.scheduling_stall import parse_ts, fetch_abandoned

STALL_HOURS = 4
MAX_LEAD_AGE_DAYS = 7

LABEL_NEW = "lead-novo"
LABEL_CADASTRO = "cadastro-abandonado"
LABEL_AGENDAMENTO = "agendamento-abandonado"
LEAD_LABELS = (LABEL_NEW, LABEL_CADASTRO, LABEL_AGENDAMENTO)

# Eventos phone-keyed (tabela events) para memória entre rodadas do cron.
LABEL_SET_EVENT = "lead_label_set"       # metadata: {"label": <str|None>, "conversation_id": int}
NUDGE_EVENT = "lead_stall_nudge_sent"
REPORT_EVENT = "lead_stall_reported"


def classify_situation(
    *,
    active: bool,
    has_appointment: bool,
    offered_abandoned: bool,
    registration_complete: bool,
    has_name: bool,
    last_msg_at: datetime,
    now: datetime,
    stall_hours: int = STALL_HOURS,
) -> str | None:
    """Devolve a label da situação do lead, ou None quando não há label a aplicar.

    Ordem importa: quem tem consulta ou está pausado sai fora; agendamento
    abandonado precede o resto (implica cadastro completo); só então avaliamos o
    cadastro incompleto."""
    if not active:
        return None
    if has_appointment:
        return None
    if offered_abandoned:
        return LABEL_AGENDAMENTO
    if registration_complete:
        return None
    silent = (now - last_msg_at) >= timedelta(hours=stall_hours)
    if silent:
        return LABEL_CADASTRO if has_name else None
    return LABEL_NEW


def select_recent_phones(
    message_rows: list[dict],
    now: datetime,
    max_age_days: int = MAX_LEAD_AGE_DAYS,
) -> dict[str, datetime]:
    """Por telefone, o instante da última mensagem DO PACIENTE (role user) dentro
    da janela de max_age_days. Ignora mensagens do assistant e leads antigos —
    isso impede o primeiro deploy de acordar abandono velho."""
    cutoff = now - timedelta(days=max_age_days)
    latest: dict[str, datetime] = {}
    for row in message_rows:
        phone = row.get("phone")
        if not phone or row.get("role") != "user":
            continue
        ts = parse_ts(row["created_at"])
        if ts < cutoff:
            continue
        if phone not in latest or ts > latest[phone]:
            latest[phone] = ts
    return latest


def label_ops(situation: str | None) -> tuple[list[str], list[str]]:
    """(add, remove) para set_labels: adiciona a label da situação (se houver) e
    remove as outras labels de lead. Nunca toca eva-ativa/eva-inativa."""
    add = [situation] if situation else []
    remove = [lbl for lbl in LEAD_LABELS if lbl != situation]
    return add, remove


def needs_label_change(situation: str | None, last_set: str | None) -> bool:
    """Só chama o Chatwoot quando a situação mudou desde a última vez que setamos."""
    return situation != last_set
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_lead_stall.py -q`
Expected: PASS (todos os testes desta tarefa).

- [ ] **Step 5: Commit**

```bash
git add app/lead_stall.py tests/test_lead_stall.py
git commit -m "feat(lead-stall): núcleo puro de classificação da situação do lead

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Orquestrador `evaluate_leads` em `app/lead_stall.py`

**Files:**
- Modify: `app/lead_stall.py`
- Test: `tests/test_lead_stall.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao FINAL de `tests/test_lead_stall.py`:

```python
# ── evaluate_leads (I/O mockado) ──────────────────────────────────────────────

class _FakeMessagesClient:
    """from_('messages').select(...).gte(...).execute() → rows fixos."""
    def __init__(self, rows):
        self._rows = rows

    def from_(self, table):
        assert table == "messages"
        return self

    def select(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    async def execute(self):
        from unittest.mock import MagicMock
        return MagicMock(data=self._rows)


async def test_evaluate_leads_classifies_each_phone(monkeypatch):
    import app.lead_stall as mod

    rows = [_msg("5581111", "user", NOW - timedelta(hours=5))]  # silêncio 5h, tem nome
    client = _FakeMessagesClient(rows)

    async def fake_fetch_abandoned(c, now, **k):
        return []  # ninguém abandonou agendamento

    async def fake_get_user(phone):
        return {"name": "Ana", "active": True}

    async def fake_upcoming(phone):
        return []

    monkeypatch.setattr(mod, "fetch_abandoned", fake_fetch_abandoned)
    monkeypatch.setattr("app.database.get_user_by_phone", fake_get_user)
    monkeypatch.setattr("app.database.get_upcoming_appointments", fake_upcoming)
    monkeypatch.setattr("app.database.is_registration_complete", lambda u: False)

    recs = await mod.evaluate_leads(client, NOW)
    assert len(recs) == 1
    assert recs[0]["phone"] == "5581111"
    assert recs[0]["situation"] == LABEL_CADASTRO
    assert recs[0]["user"]["name"] == "Ana"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_lead_stall.py::test_evaluate_leads_classifies_each_phone -q`
Expected: FAIL com `AttributeError: module 'app.lead_stall' has no attribute 'evaluate_leads'`.

- [ ] **Step 3: Implementar `evaluate_leads`**

Adicionar ao FINAL de `app/lead_stall.py`:

```python
async def evaluate_leads(client, now: datetime) -> list[dict]:
    """Para cada telefone com mensagem do paciente nos últimos MAX_LEAD_AGE_DAYS,
    reúne os fatos do banco e classifica a situação. Devolve
    [{"phone", "situation", "user", "last_msg_at"}].

    Faz I/O (Supabase + shim get_user_by_phone/get_upcoming_appointments) — a
    lógica de decisão fica em classify_situation (pura, testada à parte)."""
    from app.database import (
        get_user_by_phone, get_upcoming_appointments, is_registration_complete,
    )

    cutoff_iso = (now - timedelta(days=MAX_LEAD_AGE_DAYS)).isoformat()
    rows = (
        await client.from_("messages")
        .select("phone, role, created_at")
        .gte("created_at", cutoff_iso)
        .execute()
    ).data or []
    latest = select_recent_phones(rows, now)

    abandoned = {c["phone"] for c in await fetch_abandoned(client, now)}

    records: list[dict] = []
    for phone, last_msg_at in latest.items():
        user = await get_user_by_phone(phone) or {}
        appts = await get_upcoming_appointments(phone)
        has_appointment = any(a.get("status") == "scheduled" for a in appts)
        situation = classify_situation(
            active=bool(user.get("active", True)),
            has_appointment=has_appointment,
            offered_abandoned=phone in abandoned,
            registration_complete=is_registration_complete(user),
            has_name=bool((user.get("name") or "").strip()),
            last_msg_at=last_msg_at,
            now=now,
        )
        records.append({
            "phone": phone,
            "situation": situation,
            "user": user,
            "last_msg_at": last_msg_at,
        })
    return records
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_lead_stall.py -q`
Expected: PASS (todos, incluindo o novo).

- [ ] **Step 5: Commit**

```bash
git add app/lead_stall.py tests/test_lead_stall.py
git commit -m "feat(lead-stall): evaluate_leads orquestra fatos e classificação

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Cron de reconciliação de label + nudge

**Files:**
- Create: `scripts/send_lead_stall_nudges.py`
- Test: `tests/test_lead_stall_crons.py`

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_lead_stall_crons.py`:

```python
"""Testa o cron de situação do lead (scripts/send_lead_stall_nudges.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import scripts.send_lead_stall_nudges as cron
from app.lead_stall import LABEL_NEW, LABEL_CADASTRO, LABEL_AGENDAMENTO

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


# ── reconciliação de label ────────────────────────────────────────────────────

async def test_reconcile_writes_when_label_changed(monkeypatch):
    calls = {}

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nunca setamos label antes

    async def fake_find_or_create(phone):
        return 4242

    async def fake_set_labels(conv_id, add, remove):
        calls["conv_id"] = conv_id
        calls["add"] = add
        calls["remove"] = remove

    async def fake_log_event(event_type, phone, metadata=None):
        calls["logged"] = (event_type, metadata)

    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "get_conversation_id", lambda p: None)
    monkeypatch.setattr(cron, "find_or_create_conversation", fake_find_or_create)
    monkeypatch.setattr(cron, "set_labels", fake_set_labels)
    monkeypatch.setattr(cron, "log_event", fake_log_event)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO, "user": {}, "last_msg_at": NOW}
    await cron._reconcile_label(rec)

    assert calls["conv_id"] == 4242
    assert calls["add"] == [LABEL_CADASTRO]
    assert LABEL_NEW in calls["remove"] and LABEL_AGENDAMENTO in calls["remove"]
    assert calls["logged"][0] == "lead_label_set"


async def test_reconcile_skips_when_label_unchanged(monkeypatch):
    async def fake_get_events(phone, event_type, limit=50):
        return [{"metadata": {"label": LABEL_CADASTRO}}]

    set_labels_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "set_labels", set_labels_mock)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO, "user": {}, "last_msg_at": NOW}
    await cron._reconcile_label(rec)

    set_labels_mock.assert_not_awaited()


# ── nudge ─────────────────────────────────────────────────────────────────────

async def test_nudge_sends_for_cadastro_abandonado_active_in_window(monkeypatch):
    sent = {}

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nunca cutucado

    async def fake_window(client, phone, now):
        return True

    async def fake_send(phone, text):
        sent["phone"] = phone
        sent["text"] = text

    async def fake_log_event(event_type, phone, metadata=None):
        sent["logged"] = event_type

    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "_window_open", fake_window)
    monkeypatch.setattr(cron, "send_whatsapp", fake_send)
    monkeypatch.setattr(cron, "log_event", fake_log_event)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO,
           "user": {"name": "Ana", "active": True}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    assert sent["phone"] == "5581111"
    assert "Ana" in sent["text"]
    assert sent["logged"] == "lead_stall_nudge_sent"


async def test_nudge_skipped_when_already_nudged(monkeypatch):
    async def fake_get_events(phone, event_type, limit=50):
        return [{"metadata": {}}]  # já cutucado

    send_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "send_whatsapp", send_mock)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO,
           "user": {"name": "Ana", "active": True}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    send_mock.assert_not_awaited()


async def test_nudge_skipped_when_paused(monkeypatch):
    async def fake_get_events(phone, event_type, limit=50):
        return []

    send_mock = AsyncMock()
    monkeypatch.setattr(cron, "get_events_by_type", fake_get_events)
    monkeypatch.setattr(cron, "send_whatsapp", send_mock)

    rec = {"phone": "5581111", "situation": LABEL_CADASTRO,
           "user": {"name": "Ana", "active": False}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    send_mock.assert_not_awaited()


async def test_nudge_skipped_when_not_cadastro(monkeypatch):
    send_mock = AsyncMock()
    monkeypatch.setattr(cron, "send_whatsapp", send_mock)

    rec = {"phone": "5581111", "situation": LABEL_NEW,
           "user": {"name": "Ana", "active": True}, "last_msg_at": NOW}
    await cron._maybe_nudge(client=None, graph=None, rec=rec, now=NOW)

    send_mock.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_lead_stall_crons.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.send_lead_stall_nudges'`.

- [ ] **Step 3: Implementar o cron**

Criar `scripts/send_lead_stall_nudges.py`:

```python
"""Situação do lead: reconcilia as labels do funil no Chatwoot e cutuca quem
abandonou o cadastro. Roda a cada 30 min via GitHub Actions.

Regras (ver app/lead_stall.py):
- Reconcilia label de TODO lead recente, mas só chama o Chatwoot quando a
  situação muda (memória via evento lead_label_set) — protege do rate limit.
- Nudge só para cadastro-abandonado, ativo, dentro da janela de 24h, 8h-20h
  Recife, 1 vez por lead (evento lead_stall_nudge_sent). Espelha o
  send_scheduling_stall_nudges.
- Nada toca eva-ativa/eva-inativa nem o webhook/grafo.
"""
import asyncio
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.lead_stall import (
    evaluate_leads, label_ops, needs_label_change,
    LABEL_CADASTRO, LABEL_SET_EVENT, NUDGE_EVENT,
)
from app.database import log_event, get_events_by_type
from app.chatwoot import get_conversation_id, find_or_create_conversation, set_labels
# Helpers já batidos no cron de pagamento (normalizam dígitos→JID internamente).
from scripts.send_payment_reminders import _window_open, send_whatsapp, save_to_checkpoint

TZ = ZoneInfo("America/Recife")

WINDOW_START = 8   # não cutuca antes das 8h
WINDOW_END = 20    # nem depois das 20h


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


def cadastro_nudge_message(contact_first_name: str) -> str:
    saudacao = f"Oi, {contact_first_name}! " if contact_first_name else "Oi! "
    return (
        f"{saudacao}😊 Vi que a gente começou seu cadastro aqui na Clínica Psique "
        f"mas não chegou a finalizar. Quer continuar de onde a gente parou?\n\n"
        f"É rapidinho, é só me responder por aqui. 🙏"
    )


async def _reconcile_label(rec: dict) -> None:
    """Ajusta a label do lead no Chatwoot só quando a situação mudou desde a última
    vez que setamos (memória via evento lead_label_set)."""
    phone = rec["phone"]
    situation = rec["situation"]

    events = await get_events_by_type(phone, LABEL_SET_EVENT, limit=1)
    last_set = (events[0].get("metadata") or {}).get("label") if events else None
    if not needs_label_change(situation, last_set):
        return

    conv_id = get_conversation_id(phone)
    if conv_id is None:
        try:
            conv_id = await find_or_create_conversation(phone)
        except Exception as e:
            print(f"  [label] sem conversa Chatwoot para {phone}: {type(e).__name__}: {e}")
            return

    add, remove = label_ops(situation)
    try:
        await set_labels(conv_id, add=add, remove=remove)
    except Exception as e:
        print(f"  [label] set_labels falhou para {phone}: {type(e).__name__}: {e}")
        return  # não marca — próximo run tenta de novo

    await log_event(LABEL_SET_EVENT, phone, {"label": situation, "conversation_id": conv_id})
    print(f"  [label] {phone}: {last_set} -> {situation}")


async def _maybe_nudge(client, graph, rec: dict, now: datetime) -> None:
    """Cutuca uma vez quem abandonou o cadastro, se ativo e na janela de 24h."""
    if rec["situation"] != LABEL_CADASTRO:
        return
    phone = rec["phone"]
    user = rec["user"]

    if await get_events_by_type(phone, NUDGE_EVENT, limit=1):
        return  # já cutucado
    if not user.get("active", True):
        return  # pausado → relatório da clínica cuida
    if not await _window_open(client, phone, now):
        return  # frio (fora das 24h) → relatório da clínica cuida

    name = user.get("name") or ""
    doctor_key = user.get("preferred_doctor") or ""
    text = cadastro_nudge_message(_first_name(name))

    try:
        await send_whatsapp(phone, text)
    except Exception as e:
        print(f"  [nudge] FALHOU para {phone}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return  # não marca — próximo run tenta de novo

    if graph:
        try:
            await save_to_checkpoint(graph, phone, text, name, doctor_key)
        except Exception as e:
            print(f"  [nudge] checkpoint falhou para {phone}: {type(e).__name__}: {e}")

    await log_event(NUDGE_EVENT, phone, {"last_msg_at": rec["last_msg_at"].isoformat()})
    print(f"  [nudge] enviado para {phone}")


async def main() -> None:
    from supabase import acreate_client

    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    records = await evaluate_leads(client, now)
    print(f"Leads recentes avaliados: {len(records)}")

    # Reconciliação de label roda sempre (barata: só escreve quando muda).
    for rec in records:
        await _reconcile_label(rec)

    # Nudge só dentro da janela de horário.
    if not (WINDOW_START <= now.hour < WINDOW_END):
        print(f"Fora da janela de nudge ({WINDOW_START}h–{WINDOW_END}h). Só reconciliei labels.")
        return

    # Checkpointer do LangGraph (mesmo padrão do cron de pagamento).
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    pg_conn = None
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        pg_conn = await AsyncConnection.connect(
            conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row,
        )
        graph = build_graph(checkpointer=AsyncPostgresSaver(pg_conn))
    else:
        print("SUPABASE_CONNECTION_STRING não setado — nudge não vai para o checkpoint.")

    try:
        for rec in records:
            await _maybe_nudge(client, graph, rec, now)
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_lead_stall_crons.py -q`
Expected: PASS (todos os testes desta tarefa).

- [ ] **Step 5: Commit**

```bash
git add scripts/send_lead_stall_nudges.py tests/test_lead_stall_crons.py
git commit -m "feat(lead-stall): cron de reconciliação de label + nudge de cadastro abandonado

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Seção de cadastro abandonado no relatório diário

**Files:**
- Modify: `scripts/send_scheduling_stall_report.py`
- Test: `tests/test_lead_stall_crons.py`

Objetivo: no e-mail diário que a clínica já recebe, adicionar uma seção listando os `cadastro-abandonado` que NÃO serão cutucados (pausados ou fora da janela de 24h) e ainda não reportados. Sem criar e-mail novo. O comportamento da seção de agendamento fica intacto.

- [ ] **Step 1: Escrever o teste que falha (helper de seleção do relatório)**

Adicionar ao FINAL de `tests/test_lead_stall_crons.py`:

```python
# ── relatório: seleção de cadastro-abandonado frio ────────────────────────────

import scripts.send_scheduling_stall_report as report
from app.lead_stall import REPORT_EVENT


async def test_report_selects_cold_cadastro_abandonado(monkeypatch):
    async def fake_evaluate(client, now):
        return [
            {"phone": "5581111", "situation": LABEL_CADASTRO,
             "user": {"name": "Ana", "active": False}, "last_msg_at": NOW},   # pausado → reportável
            {"phone": "5582222", "situation": LABEL_CADASTRO,
             "user": {"name": "Bia", "active": True}, "last_msg_at": NOW},    # ativo+janela → nudge cuida
            {"phone": "5583333", "situation": LABEL_NEW,
             "user": {"name": "Cid", "active": True}, "last_msg_at": NOW},    # não é cadastro-abandonado
        ]

    async def fake_window(client, phone, now):
        return True  # 5582222 está na janela

    async def fake_get_events(phone, event_type, limit=50):
        return []  # nada reportado ainda

    monkeypatch.setattr(report, "evaluate_leads", fake_evaluate)
    monkeypatch.setattr(report, "_window_open_safe", fake_window)
    monkeypatch.setattr(report, "get_events_by_type", fake_get_events)

    got = await report.fetch_cadastro_abandonado_reportable(client=None, now=NOW)
    assert [c["phone"] for c in got] == ["5581111"]
    assert got[0]["name"] == "Ana"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_lead_stall_crons.py::test_report_selects_cold_cadastro_abandonado -q`
Expected: FAIL com `AttributeError: module 'scripts.send_scheduling_stall_report' has no attribute 'fetch_cadastro_abandonado_reportable'`.

- [ ] **Step 3: Implementar o helper e integrar no e-mail**

Em `scripts/send_scheduling_stall_report.py`, adicionar os imports no topo (após os imports existentes de `app.scheduling_stall`):

```python
from app.lead_stall import evaluate_leads, LABEL_CADASTRO, REPORT_EVENT
from app.database import get_events_by_type
```

Adicionar as duas funções novas (antes de `main`):

```python
async def fetch_cadastro_abandonado_reportable(client, now: datetime) -> list[dict]:
    """cadastro-abandonado que a Eva NÃO vai cutucar (pausado OU fora da janela de
    24h) e que ainda não foi reportado. Espelha a regra do relatório de
    agendamento."""
    records = await evaluate_leads(client, now)
    reportable: list[dict] = []
    for rec in records:
        if rec["situation"] != LABEL_CADASTRO:
            continue
        phone = rec["phone"]
        user = rec["user"]
        active = bool(user.get("active", True))
        window = await _window_open_safe(client, phone, now) if active else False
        if active and window:
            continue  # o cron de nudge cuida deste
        if await get_events_by_type(phone, REPORT_EVENT, limit=1):
            continue  # já reportado
        reportable.append({
            "phone": phone,
            "name": user.get("name") or "(sem cadastro)",
            "active": active,
            "last_msg_at": rec["last_msg_at"],
        })
    return reportable


def _fmt_cadastro_case(case: dict) -> str:
    quando = case["last_msg_at"].astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
    motivo = "Eva pausada (eva-inativa)" if not case["active"] else "fora da janela de 24h"
    line = f"• {case['name']}"
    line += f"\n  WhatsApp: {case['phone']}"
    line += f"\n  Última mensagem em: {quando}"
    line += f"\n  Motivo do contato manual: {motivo}"
    return line
```

Agora integrar no `main()`. Substituir o corpo atual de `main()` para compor as DUAS seções e enviar se QUALQUER uma tiver casos. O bloco de agendamento existente é preservado; só muda a decisão de envio e a marcação. Substituir de `cases = await fetch_abandoned(client, now)` até o final da função por:

```python
    # Seção 1: agendamento abandonado (comportamento existente).
    cases = await fetch_abandoned(client, now)
    reportable: list[dict] = []
    for case in cases:
        phone = case["phone"]
        user = await get_user_by_phone(phone) or {}
        active = bool(user.get("active"))
        window = await _window_open_safe(client, phone, now) if active else False
        if is_nudge_eligible(active, window):
            continue
        case["name"] = user.get("name") or "(sem cadastro)"
        case["active"] = active
        reportable.append(case)

    # Seção 2: cadastro abandonado frio (novo).
    cadastro_cases = await fetch_cadastro_abandonado_reportable(client, now)

    if not reportable and not cadastro_cases:
        print("Nenhum caso para reportar — e-mail não enviado.")
        return

    today_str = now.strftime("%d/%m/%Y")
    lines: list[str] = []

    if reportable:
        lines += [
            f"Pacientes que começaram a agendar e não confirmaram — {today_str}",
            "=" * 60,
            "Viram horários com a Eva mas não fecharam a consulta, e NÃO estão",
            "sendo cutucados automaticamente (Eva pausada, ou fora da janela de",
            "24h do WhatsApp). Vale um contato manual.",
            "",
            f"Total: {len(reportable)}",
            "-" * 60,
            "",
        ]
        for case in reportable:
            lines.append(_fmt_case(case))
            lines.append("")

    if cadastro_cases:
        lines += [
            f"Leads que começaram o cadastro e não terminaram — {today_str}",
            "=" * 60,
            "Informaram o nome mas não concluíram o cadastro, e NÃO estão sendo",
            "cutucados automaticamente (Eva pausada, ou fora da janela de 24h).",
            "Vale um contato manual.",
            "",
            f"Total: {len(cadastro_cases)}",
            "-" * 60,
            "",
        ]
        for case in cadastro_cases:
            lines.append(_fmt_cadastro_case(case))
            lines.append("")

    body = "\n".join(lines)
    total = len(reportable) + len(cadastro_cases)
    subject = f"Psique — Leads não finalizados ({total}) — {today_str}"

    print(body)
    print()

    missing = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "CLINIC_NOTIFY_EMAIL")
               if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"Variáveis de ambiente ausentes: {', '.join(missing)} — e-mail NÃO enviado.")

    from app.email_sender import send_clinic_notification_email
    await send_clinic_notification_email(subject, body)

    # "Avisou, não repete": marca cada caso só APÓS o e-mail sair.
    for case in reportable:
        await mark_handled(client, case["phone"], REPORT_EVENT_SCHED,
                           {"offered_at": case["offered_at"].isoformat()})
    for case in cadastro_cases:
        await mark_handled(client, case["phone"], REPORT_EVENT,
                           {"last_msg_at": case["last_msg_at"].isoformat()})
    print(f"E-mail enviado: {len(reportable)} agendamento(s) + {len(cadastro_cases)} cadastro(s).")
```

Atenção ao conflito de nomes: o módulo já importa `REPORT_EVENT` de `app.scheduling_stall` (agendamento) e agora também de `app.lead_stall` (cadastro). Para evitar a colisão, no import existente de `app.scheduling_stall` renomear via alias. Trocar a linha de import de `app.scheduling_stall` para:

```python
from app.scheduling_stall import (
    fetch_abandoned, is_nudge_eligible, REPORT_EVENT as REPORT_EVENT_SCHED, mark_handled,
)
```

- [ ] **Step 4: Rodar os testes do relatório e a suíte de agendamento (garantir que não quebrou)**

Run: `uv run pytest tests/test_lead_stall_crons.py tests/test_scheduling_stall.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_scheduling_stall_report.py tests/test_lead_stall_crons.py
git commit -m "feat(lead-stall): seção de cadastro abandonado no relatório diário

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Workflow do GitHub Actions

**Files:**
- Create: `.github/workflows/lead_stall_nudges.yml`

- [ ] **Step 1: Criar o workflow (espelha appointment/scheduling)**

Antes de escrever, abrir `.github/workflows/scheduling_stall_nudges.yml` e copiar a estrutura exata (checkout, setup-uv, uv sync, env vars, comando). Criar `.github/workflows/lead_stall_nudges.yml` com o mesmo esqueleto, trocando:
- `name:` para `Send lead stall nudges`.
- `schedule: cron:` para `"*/30 * * * *"`.
- o comando final para `uv run python scripts/send_lead_stall_nudges.py`.
- garantir que a lista de `env:` inclua as mesmas variáveis do `scheduling_stall_nudges.yml` (SUPABASE_URL, SUPABASE_KEY, SUPABASE_CONNECTION_STRING, CHATWOOT_*, WHATSAPP/META de envio, SMTP não é necessário aqui).

- [ ] **Step 2: Validar YAML**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/lead_stall_nudges.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Conferir que os secrets/env batem com o cron irmão**

Run: `diff <(grep -oE '[A-Z_]+' .github/workflows/scheduling_stall_nudges.yml | sort -u) <(grep -oE '[A-Z_]+' .github/workflows/lead_stall_nudges.yml | sort -u)`
Expected: sem diferenças relevantes de variáveis de ambiente (fora `name`/schedule/comando). Ajustar se faltar alguma env.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/lead_stall_nudges.yml
git commit -m "ci(lead-stall): workflow do cron a cada 30 min

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Suíte completa + revisão final

**Files:** nenhum (verificação).

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS (nenhuma regressão nos testes existentes).

- [ ] **Step 2: Conferência de fumaça do import do cron**

Run: `uv run python -c "import scripts.send_lead_stall_nudges as c; import app.lead_stall as m; print('import ok', bool(c.main), bool(m.evaluate_leads))"`
Expected: `import ok True True`.

- [ ] **Step 3: Commit final se algo mudou** (senão, seguir para o PR)

```bash
git add -A && git commit -m "test(lead-stall): garante suíte verde" || echo "nada a commitar"
```

---

## Notas de operação (para o handoff, não são passos de código)

- Primeiro deploy: por causa do limite de 7 dias em `MAX_LEAD_AGE_DAYS` e da janela de 24h do WhatsApp, o cron não vai cutucar abandono antigo. Ainda assim, vale rodar 1x manual (`workflow_dispatch`) e conferir o log antes de confiar no agendamento.
- As labels `lead-novo`/`cadastro-abandonado`/`agendamento-abandonado` precisam existir no Chatwoot? O `set_labels` cria a associação na conversa; confirmar no painel do Chatwoot se labels novas aparecem automaticamente ou se precisam ser pré-cadastradas em Labels. Se precisarem, criar as três à mão uma vez.
- Depois de mergeado: `git worktree remove .worktrees/situacao-lead`.

---

## Self-review (preenchido pelo autor do plano)

- **Cobertura do spec:** rastreio de cadastro abandonado (Task 2,3), nudge 4h com corte por nome (Task 1 classify + Task 3 nudge), três labels (Task 1,3), relatório dobrado (Task 4), salvaguardas de idade/pausa/rate-limit/idempotência (Task 1 MAX_LEAD_AGE, Task 3 reconcile-on-change + get_events markers), reúso do scheduling_stall (Task 1 import), tudo em cron (Tasks 3-5), testes por camada (Tasks 1-4). Coberto.
- **Placeholders:** nenhum "TBD/TODO"; todo passo de código tem o código real.
- **Consistência de tipos/nomes:** `classify_situation`, `evaluate_leads`, `label_ops`, `needs_label_change`, `_reconcile_label`, `_maybe_nudge`, `fetch_cadastro_abandonado_reportable`, `cadastro_nudge_message` usados com a mesma assinatura em todas as tarefas; `REPORT_EVENT` (lead) vs `REPORT_EVENT_SCHED` (agendamento) desambiguados na Task 4.
