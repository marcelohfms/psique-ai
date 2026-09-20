# Relatório de funil de leads — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um relatório semanal do funil de leads (Interessados → Qualificados → Agendados-pagos, mais Pendentes e Perdidos), enviado por e-mail SÓ para a dona, read-only sobre `events` e `messages`, sem tocar o webhook nem o grafo.

**Architecture:** Um módulo puro (`app/funnel_report.py`) calcula o funil a partir de conjuntos de telefones por etapa + última atividade. Um cron (`scripts/send_funnel_report.py`) lê `events` e `messages` da janela de 30 dias, monta os conjuntos, chama a lógica pura e envia via uma função nova `send_report_email` (destinatário explícito, sem fallback para a clínica). Workflow semanal.

**Tech Stack:** Python, asyncio, Supabase (postgrest async), SMTP, pytest + pytest-asyncio.

**Referências de leitura:**
- `app/scheduling_stall.py` — `parse_ts`, e o padrão de `fetch_abandoned` (ler eventos via client).
- `app/email_sender.py` — `_send_email(smtp_host, smtp_port, smtp_user, smtp_password, to_email, subject, body)` (linha 23) e `send_clinic_notification_email` (linha 43) como modelo.
- `app/database.py` — `get_user_by_phone` (:134) para o nome do pendente.
- `scripts/send_scheduling_stall_report.py` — padrão de cron de relatório por e-mail.
- `tests/test_scheduling_stall.py` — padrão de teste puro com client fake.

**Convenções:** rodar tudo dentro da worktree `.worktrees/relatorio-funil`. Testes: `uv run pytest --tb=short`. Commits pequenos por tarefa, cada um terminando com `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create** `app/funnel_report.py` — lógica pura: constantes, `etapa_alcancada`, `compute_funnel`, `format_report`.
- **Modify** `app/email_sender.py` — `send_report_email(subject, body, to_email)`.
- **Create** `scripts/send_funnel_report.py` — cron: lê events+messages, monta conjuntos, calcula, enriquece nomes, envia.
- **Create** `.github/workflows/funnel_report.yml` — agenda semanal.
- **Modify** `.env.example` — documentar `FUNNEL_REPORT_EMAIL`.
- **Create** `tests/test_funnel_report.py` — lógica pura.
- **Create** `tests/test_funnel_report_cron.py` — cron + `send_report_email`, com mocks.

---

## Task 1: Núcleo puro em `app/funnel_report.py`

**Files:**
- Create: `app/funnel_report.py`
- Test: `tests/test_funnel_report.py`

- [ ] **Step 1: Criar `tests/test_funnel_report.py` com este conteúdo exato:**

```python
"""Testa a lógica pura do relatório de funil (app/funnel_report.py)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.funnel_report import (
    etapa_alcancada, compute_funnel, format_report,
    STALL_DAYS,
)

TZ = ZoneInfo("America/Recife")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── etapa_alcancada ───────────────────────────────────────────────────────────

def test_etapa_paid_is_agendado():
    assert etapa_alcancada("p", qualified=set(), booked=set(), paid={"p"}) == "Agendado"


def test_etapa_booked_unpaid():
    assert etapa_alcancada("p", qualified={"p"}, booked={"p"}, paid=set()) == "Agendou, falta pagar"


def test_etapa_qualified():
    assert etapa_alcancada("p", qualified={"p"}, booked=set(), paid=set()) == "Qualificado"


def test_etapa_interessado():
    assert etapa_alcancada("p", qualified=set(), booked=set(), paid=set()) == "Interessado"


# ── compute_funnel ────────────────────────────────────────────────────────────

def test_compute_funnel_counts_and_buckets():
    cohort = {"a", "b", "c", "d", "e"}
    qualified = {"a", "b", "c", "d"}      # e nunca qualificou
    booked = {"a", "b", "c"}
    paid = {"a", "b"}                      # só a e b converteram
    last_activity = {
        "c": NOW - timedelta(days=1),      # não-convertido, quente → pendente
        "d": NOW - timedelta(days=10),     # não-convertido, frio → perdido
        "e": NOW - timedelta(days=2),      # não-convertido, quente → pendente
    }
    r = compute_funnel(cohort=cohort, qualified=qualified, booked=booked,
                       paid=paid, last_activity=last_activity, now=NOW)
    assert r["interessados"] == 5
    assert r["qualificados"] == 4
    assert r["agendados"] == 2
    assert r["conversao_pct"] == 40
    assert {p["phone"] for p in r["pendentes"]} == {"c", "e"}
    assert r["perdidos"] == 1
    # c chegou até "agendou, falta pagar"; e parou em interessado
    etapas = {p["phone"]: p["etapa"] for p in r["pendentes"]}
    assert etapas["c"] == "Agendou, falta pagar"
    assert etapas["e"] == "Interessado"


def test_compute_funnel_no_activity_is_perdido():
    r = compute_funnel(cohort={"x"}, qualified=set(), booked=set(), paid=set(),
                       last_activity={}, now=NOW)
    assert r["perdidos"] == 1
    assert r["pendentes"] == []


def test_compute_funnel_empty_cohort():
    r = compute_funnel(cohort=set(), qualified=set(), booked=set(), paid=set(),
                       last_activity={}, now=NOW)
    assert r["interessados"] == 0
    assert r["conversao_pct"] == 0


# ── format_report ─────────────────────────────────────────────────────────────

def test_format_report_has_sections_and_names():
    result = {
        "interessados": 5, "qualificados": 4, "agendados": 2, "conversao_pct": 40,
        "pendentes": [{"phone": "5581111", "etapa": "Qualificado", "name": "Ana"},
                      {"phone": "5582222", "etapa": "Interessado", "name": ""}],
        "perdidos": 1,
    }
    subject, body = format_report(result, NOW, TZ)
    assert "Funil de leads" in body
    assert "Interessados" in body and "5" in body
    assert "Conversão: 2 de 5 (40%)" in body
    assert "Ana" in body and "5581111" in body    # pendente com nome
    assert "5582222" in body                        # pendente sem nome cai pro telefone
    assert "2/5" in subject or "2 de 5" in subject or "2/5" in subject
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_funnel_report.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.funnel_report'`.

- [ ] **Step 3: Criar `app/funnel_report.py` com este conteúdo exato:**

```python
"""Relatório de funil de leads (lógica pura, sem I/O).

Etapas do funil, por telefone, dentro de um cohort de chegada:
  Interessados → Qualificados → Agendados (agendou E pagou/isento).
Quem não converteu vira Pendente (ativo nos últimos STALL_DAYS dias) ou
Perdido (frio). "Agendado" só conta com a taxa resolvida (evento de pagamento
ou isenção), por decisão de produto.

Consumido por scripts/send_funnel_report.py (lê events+messages e chama isto).
"""
from datetime import datetime, timedelta

FUNNEL_WINDOW_DAYS = 30   # cohort: chegou nos últimos 30 dias
STALL_DAYS = 7            # separa pendente (quente) de perdido (frio)

# Tipos de evento (tabela events) que marcam cada etapa.
ARRIVAL_EVENT = "conversation_started"
QUALIFIED_EVENT = "slots_offered"
BOOKED_EVENT = "appointment_booked"
PAID_EVENTS = ("payment_receipt_registered", "booking_fee_registered", "booking_fee_waived")


def etapa_alcancada(phone: str, *, qualified: set, booked: set, paid: set) -> str:
    """A etapa mais avançada que o telefone alcançou."""
    if phone in paid:
        return "Agendado"
    if phone in booked:
        return "Agendou, falta pagar"
    if phone in qualified:
        return "Qualificado"
    return "Interessado"


def compute_funnel(*, cohort: set, qualified: set, booked: set, paid: set,
                   last_activity: dict, now: datetime, stall_days: int = STALL_DAYS) -> dict:
    """Conta o funil e separa os não-convertidos em pendentes/perdidos.

    - Convertido = tem pagamento/isenção resolvido (está em `paid`).
    - Qualificado (contagem) = alcançou ao menos a etapa de ver horários
      (qualified ∪ booked ∪ paid), tudo dentro do cohort.
    - Pendente = não-convertido com última atividade nos últimos stall_days.
    - Perdido = não-convertido frio (sem atividade recente)."""
    converted = cohort & paid
    reached_qualified = cohort & (qualified | booked | paid)
    non_converted = cohort - converted

    threshold = now - timedelta(days=stall_days)
    pendentes: list[dict] = []
    perdidos = 0
    for phone in non_converted:
        la = last_activity.get(phone)
        if la is not None and la >= threshold:
            pendentes.append({
                "phone": phone,
                "etapa": etapa_alcancada(phone, qualified=qualified, booked=booked, paid=paid),
            })
        else:
            perdidos += 1

    interessados = len(cohort)
    agendados = len(converted)
    conversao_pct = round(100 * agendados / interessados) if interessados else 0
    pendentes.sort(key=lambda p: p["phone"])

    return {
        "interessados": interessados,
        "qualificados": len(reached_qualified),
        "agendados": agendados,
        "conversao_pct": conversao_pct,
        "pendentes": pendentes,
        "perdidos": perdidos,
    }


def format_report(result: dict, now: datetime, tz) -> tuple[str, str]:
    """Monta (assunto, corpo) do e-mail. Os pendentes podem trazer 'name'."""
    hoje = now.astimezone(tz).strftime("%d/%m/%Y")
    q_drop = result["interessados"] - result["qualificados"]
    a_drop = result["qualificados"] - result["agendados"]

    lines = [
        f"Funil de leads — {hoje}",
        "=" * 50,
        f"Interessados   {result['interessados']}",
        f"Qualificados   {result['qualificados']}   (-{q_drop} no cadastro)",
        f"Agendados      {result['agendados']}   (-{a_drop} entre qualificar e pagar)",
        "",
        f"Conversão: {result['agendados']} de {result['interessados']} ({result['conversao_pct']}%)",
        "",
        f"Pendentes (quentes, ativos até {STALL_DAYS} dias): {len(result['pendentes'])}",
        f"Perdidos (frios): {result['perdidos']}",
        "",
        "-" * 50,
        "Pendentes para contato:",
    ]
    if result["pendentes"]:
        for p in result["pendentes"]:
            nome = (p.get("name") or "").strip()
            quem = f"{nome} ({p['phone']})" if nome else p["phone"]
            lines.append(f"• {quem} — {p['etapa']}")
    else:
        lines.append("(nenhum)")

    body = "\n".join(lines)
    subject = f"Psique — Funil de leads ({result['agendados']}/{result['interessados']}) — {hoje}"
    return subject, body
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_funnel_report.py -q`
Expected: PASS (todos). Depois `uv run pytest --tb=short -q` para garantir zero regressão.

- [ ] **Step 5: Commit**

```bash
git add app/funnel_report.py tests/test_funnel_report.py
git commit -m "feat(funnel): núcleo puro do relatório de funil

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `send_report_email` em `app/email_sender.py`

**Files:**
- Modify: `app/email_sender.py`
- Test: `tests/test_funnel_report_cron.py`

- [ ] **Step 1: Criar `tests/test_funnel_report_cron.py` com este conteúdo (só os testes de e-mail por enquanto):**

```python
"""Testa o cron de relatório de funil (scripts/send_funnel_report.py) e o envio."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── send_report_email ─────────────────────────────────────────────────────────

async def test_send_report_email_uses_explicit_recipient(monkeypatch):
    import app.email_sender as es
    captured = {}

    def fake_send_email(host, port, user, password, to_email, subject, body):
        captured["to"] = to_email
        captured["subject"] = subject

    monkeypatch.setattr(es, "_send_email", fake_send_email)
    with monkeypatch.context() as m:
        m.setenv("SMTP_HOST", "smtp.example.com")
        m.setenv("SMTP_USER", "u@example.com")
        m.setenv("SMTP_PASSWORD", "pw")
        await es.send_report_email("assunto", "corpo", "dona@example.com")

    assert captured["to"] == "dona@example.com"
    assert captured["subject"] == "assunto"


async def test_send_report_email_raises_without_recipient(monkeypatch):
    import app.email_sender as es
    with monkeypatch.context() as m:
        m.setenv("SMTP_HOST", "smtp.example.com")
        m.setenv("SMTP_USER", "u@example.com")
        m.setenv("SMTP_PASSWORD", "pw")
        with pytest.raises(RuntimeError):
            await es.send_report_email("assunto", "corpo", "")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_funnel_report_cron.py -q`
Expected: FAIL com `AttributeError: module 'app.email_sender' has no attribute 'send_report_email'`.

- [ ] **Step 3: Em `app/email_sender.py`, adicionar esta função logo após `send_clinic_notification_email` (por volta da linha 83):**

```python
async def send_report_email(subject: str, body: str, to_email: str) -> None:
    """Envia um e-mail de relatório para um destinatário EXPLÍCITO (não usa
    CLINIC_NOTIFY_EMAIL). Usado pelo relatório de funil, que por ora vai só para
    a dona. Falha explícito se faltar SMTP ou o destinatário."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    missing = [
        name for name, value in (
            ("SMTP_HOST", smtp_host), ("SMTP_USER", smtp_user),
            ("SMTP_PASSWORD", smtp_password), ("destinatário", to_email),
        ) if not value
    ]
    if missing:
        raise RuntimeError(
            f"E-mail de relatório NÃO enviado ({subject!r}) — faltando: {', '.join(missing)}"
        )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _send_email, smtp_host, smtp_port, smtp_user, smtp_password,
        to_email, subject, body,
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_funnel_report_cron.py -q`
Expected: PASS (os dois testes de e-mail).

- [ ] **Step 5: Commit**

```bash
git add app/email_sender.py tests/test_funnel_report_cron.py
git commit -m "feat(funnel): send_report_email com destinatário explícito

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Cron `scripts/send_funnel_report.py`

**Files:**
- Create: `scripts/send_funnel_report.py`
- Test: `tests/test_funnel_report_cron.py` (apêndice)

- [ ] **Step 1: APÊNDICE ao final de `tests/test_funnel_report_cron.py`:**

```python
# ── cron: montagem dos conjuntos e envio ──────────────────────────────────────

import scripts.send_funnel_report as cron


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *a, **k):
        return self
    def in_(self, *a, **k):
        return self
    def gte(self, *a, **k):
        return self
    async def execute(self):
        return MagicMock(data=self._rows)


class _FakeClient:
    def __init__(self, by_table):
        self._by_table = by_table
    def from_(self, table):
        return _FakeQuery(self._by_table.get(table, []))


async def test_cron_builds_sets_and_sends(monkeypatch):
    events = [
        {"phone": "5581aaa", "event_type": "conversation_started", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581aaa", "event_type": "slots_offered", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581aaa", "event_type": "appointment_booked", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581aaa", "event_type": "payment_receipt_registered", "created_at": (NOW - timedelta(days=2)).isoformat()},
        {"phone": "5581bbb", "event_type": "conversation_started", "created_at": (NOW - timedelta(days=1)).isoformat()},
        {"phone": "5581bbb", "event_type": "slots_offered", "created_at": (NOW - timedelta(days=1)).isoformat()},
    ]
    messages = [
        {"phone": "5581bbb", "role": "user", "created_at": (NOW - timedelta(days=1)).isoformat()},
    ]
    client = _FakeClient({"events": events, "messages": messages})

    async def fake_get_user(phone):
        return {"name": "Bruno"} if phone == "5581bbb" else {}

    sent = {}
    async def fake_send(subject, body, to_email):
        sent["to"] = to_email
        sent["body"] = body

    monkeypatch.setattr(cron, "get_user_by_phone", fake_get_user)
    monkeypatch.setattr(cron, "send_report_email", fake_send)
    monkeypatch.setenv("FUNNEL_REPORT_EMAIL", "dona@example.com")

    await cron.run(client, NOW)

    assert sent["to"] == "dona@example.com"
    # aaa converteu (pagou), bbb é pendente qualificado
    assert "Conversão: 1 de 2" in sent["body"]
    assert "Bruno" in sent["body"] and "5581bbb" in sent["body"]


async def test_cron_raises_without_recipient(monkeypatch):
    client = _FakeClient({"events": [], "messages": []})
    monkeypatch.delenv("FUNNEL_REPORT_EMAIL", raising=False)
    with pytest.raises(EnvironmentError):
        await cron.run(client, NOW)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_funnel_report_cron.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.send_funnel_report'`.

- [ ] **Step 3: Criar `scripts/send_funnel_report.py` com este conteúdo exato:**

```python
"""Relatório semanal de funil de leads. Roda via GitHub Actions.

Read-only sobre `events` e `messages`. Envia SÓ para a dona (FUNNEL_REPORT_EMAIL),
nunca para a clínica. Ver app/funnel_report.py para as etapas e regras.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.funnel_report import (
    compute_funnel, format_report,
    FUNNEL_WINDOW_DAYS, ARRIVAL_EVENT, QUALIFIED_EVENT, BOOKED_EVENT, PAID_EVENTS,
)
from app.scheduling_stall import parse_ts
from app.database import get_user_by_phone
from app.email_sender import send_report_email

TZ = ZoneInfo("America/Recife")

_FUNNEL_EVENT_TYPES = [ARRIVAL_EVENT, QUALIFIED_EVENT, BOOKED_EVENT, *PAID_EVENTS]


async def run(client, now: datetime) -> None:
    """Lê os dados, calcula o funil e envia. `client` e `now` injetados para teste."""
    to_email = os.environ.get("FUNNEL_REPORT_EMAIL")
    if not to_email:
        raise EnvironmentError(
            "FUNNEL_REPORT_EMAIL não setado — relatório de funil NÃO enviado. "
            "Setar com o e-mail da dona (não usar o da clínica)."
        )

    cutoff = (now - timedelta(days=FUNNEL_WINDOW_DAYS)).astimezone(timezone.utc).isoformat()

    rows = (
        await client.from_("events")
        .select("phone, event_type, created_at")
        .in_("event_type", _FUNNEL_EVENT_TYPES)
        .gte("created_at", cutoff)
        .execute()
    ).data or []

    cohort, qualified, booked, paid = set(), set(), set(), set()
    for r in rows:
        phone = r.get("phone")
        if not phone:
            continue
        et = r.get("event_type")
        if et == ARRIVAL_EVENT:
            cohort.add(phone)
        elif et == QUALIFIED_EVENT:
            qualified.add(phone)
        elif et == BOOKED_EVENT:
            booked.add(phone)
        elif et in PAID_EVENTS:
            paid.add(phone)

    mrows = (
        await client.from_("messages")
        .select("phone, role, created_at")
        .gte("created_at", cutoff)
        .execute()
    ).data or []
    last_activity: dict[str, datetime] = {}
    for m in mrows:
        phone = m.get("phone")
        if not phone or m.get("role") != "user":
            continue
        ts = parse_ts(m["created_at"])
        if phone not in last_activity or ts > last_activity[phone]:
            last_activity[phone] = ts

    result = compute_funnel(
        cohort=cohort, qualified=qualified, booked=booked, paid=paid,
        last_activity=last_activity, now=now,
    )

    # Enriquece os pendentes com o nome (o e-mail vai só para a dona).
    for p in result["pendentes"]:
        user = await get_user_by_phone(p["phone"]) or {}
        p["name"] = user.get("patient_name") or user.get("name") or ""

    subject, body = format_report(result, now, TZ)
    print(body)
    await send_report_email(subject, body, to_email)
    print(f"Relatório de funil enviado para {to_email}.")


async def main() -> None:
    from supabase import acreate_client
    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    await run(client, now)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_funnel_report_cron.py -q`
Expected: PASS (todos). Depois `uv run pytest --tb=short -q` (zero regressão).

- [ ] **Step 5: Commit**

```bash
git add scripts/send_funnel_report.py tests/test_funnel_report_cron.py
git commit -m "feat(funnel): cron do relatório de funil (só para a dona)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Workflow + .env.example

**Files:**
- Create: `.github/workflows/funnel_report.yml`
- Modify: `.env.example`

- [ ] **Step 1: Ler `.github/workflows/scheduling_stall_report.yml`** (relatório por e-mail que já existe) para copiar a estrutura, inclusive o bloco `env:` com as SMTP e SUPABASE.

- [ ] **Step 2: Criar `.github/workflows/funnel_report.yml`** com o mesmo esqueleto, mudando:
  - `name:` → `Send funnel report`
  - `schedule: - cron:` → `"0 11 * * 1"` (segunda, ~8h Recife)
  - manter `workflow_dispatch:`
  - comando final → `uv run python scripts/send_funnel_report.py`
  - bloco `env:` deve conter: `SUPABASE_URL`, `SUPABASE_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, e `FUNNEL_REPORT_EMAIL: ${{ secrets.FUNNEL_REPORT_EMAIL }}`. NÃO incluir `CLINIC_NOTIFY_EMAIL` (o relatório não vai para a clínica).

- [ ] **Step 3: Validar YAML**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/funnel_report.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 4: Documentar a env em `.env.example`.** Acrescentar uma linha (perto das outras de e-mail):
```
FUNNEL_REPORT_EMAIL=email_da_dona@exemplo.com  # relatório de funil vai SÓ para cá, nunca para a clínica
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/funnel_report.yml .env.example
git commit -m "ci(funnel): workflow semanal do relatório de funil

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Suíte completa + verificação

**Files:** nenhum.

- [ ] **Step 1: Suíte inteira**

Run: `uv run pytest --tb=short -q`
Expected: PASS (sem regressão).

- [ ] **Step 2: Smoke import**

Run: `uv run python -c "import scripts.send_funnel_report as c; import app.funnel_report as m; from app.email_sender import send_report_email; print('ok', bool(c.run), bool(m.compute_funnel))"`
Expected: `ok True True`

- [ ] **Step 3: Commit final se necessário**

```bash
git add -A && git commit -m "test(funnel): suíte verde" || echo "nada a commitar"
```

---

## Notas de operação (handoff, não são passos de código)

- Setar o secret `FUNNEL_REPORT_EMAIL` no GitHub (e no `.env` do servidor, se rodar de lá) com o e-mail da dona. Sem isso o workflow falha de propósito (e não manda nada para a clínica).
- Rodar 1x manual (`workflow_dispatch`) e conferir o e-mail recebido.
- Quando a feature for fechada com a clínica, trocar/estender o destinatário (env var), sem mudar código.
- Depois de mergeado: `git worktree remove .worktrees/relatorio-funil`.

---

## Self-review (autor do plano)

- **Cobertura do spec:** etapas e regra "agendado = pago" (Task 1 `etapa_alcancada`/`compute_funnel`); pendente/perdido por 7 dias (Task 1); cohort 30 dias (Task 1 constante + Task 3 query); entrega só para a dona sem fallback (Task 2 `send_report_email` + Task 3 exige `FUNNEL_REPORT_EMAIL`); lista de pendentes com nome (Task 3 enriquece + Task 1 `format_report`); read-only (Task 3 só lê); semanal (Task 4). Coberto.
- **Placeholders:** nenhum; todo passo tem código real.
- **Consistência de nomes:** `etapa_alcancada`, `compute_funnel`, `format_report`, `send_report_email`, `run`, e as constantes `ARRIVAL_EVENT`/`QUALIFIED_EVENT`/`BOOKED_EVENT`/`PAID_EVENTS`/`FUNNEL_WINDOW_DAYS`/`STALL_DAYS` usados iguais em todas as tarefas.
