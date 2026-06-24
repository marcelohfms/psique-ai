# Blindagem de data/dia-da-semana/hoje-amanhã da Eva — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar erros da Eva com dia da semana e hoje/amanhã, calculando esses fatos em Python e injetando-os prontos no prompt, mais uma ferramenta determinística para datas distantes.

**Architecture:** Um módulo puro `app/dates.py` concentra toda a aritmética de calendário (dia da semana, hoje/amanhã, bloco de referência de 35 dias). `app/graph/nodes.py` injeta o bloco de referência e rotula cada consulta agendada usando esse módulo. Uma tool `consultar_data` (em `app/graph/tools.py`) cobre datas fora da janela. As instruções do prompt que pediam cálculo ao LLM são removidas/encurtadas.

**Tech Stack:** Python 3, FastAPI, LangGraph, langchain `@tool`, pytest. Timezone `America/Recife` via `zoneinfo`.

**Spec:** `docs/superpowers/specs/2026-06-24-data-dia-da-semana-design.md`

---

## File Structure

- **Create** `app/dates.py` — funções puras de calendário (única responsabilidade: data → rótulos pt-BR).
- **Create** `tests/test_dates.py` — testes unitários puros do módulo acima.
- **Modify** `app/graph/tools.py` — nova tool `consultar_data` (delega para `app/dates.py`).
- **Modify** `app/graph/nodes.py` — registrar a tool em `TOOLS`; injetar bloco de referência e rótulos nas consultas.
- **Modify** `app/graph/prompts.py` — trocar header `{today}`; remover/encurtar instruções de cálculo nos dois templates.
- **Modify** `tests/test_tools.py` — testes da tool `consultar_data`.
- **Modify** `tests/test_process_message.py` — teste de integração do prompt construído.

---

## Task 1: Módulo de calendário `app/dates.py`

**Files:**
- Create: `app/dates.py`
- Test: `tests/test_dates.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dates.py`:

```python
"""Unit tests for app/dates.py — deterministic calendar helpers."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.dates import (
    weekday_pt,
    relative_label,
    date_suffix_pt,
    format_date_pt,
    build_date_reference,
    REFERENCE_WINDOW_DAYS,
)

TZ = ZoneInfo("America/Recife")


def test_weekday_pt_known_dates():
    # 2026-06-24 is a Wednesday; 2026-06-23 a Tuesday; 2026-06-28 a Sunday
    assert weekday_pt(date(2026, 6, 24)) == "quarta-feira"
    assert weekday_pt(date(2026, 6, 23)) == "terça-feira"
    assert weekday_pt(date(2026, 6, 28)) == "domingo"


def test_relative_label_near_dates():
    today = date(2026, 6, 24)
    assert relative_label(date(2026, 6, 24), today) == "hoje"
    assert relative_label(date(2026, 6, 25), today) == "amanhã"
    assert relative_label(date(2026, 6, 26), today) == "depois de amanhã"
    assert relative_label(date(2026, 6, 27), today) is None
    assert relative_label(date(2026, 6, 23), today) is None


def test_date_suffix_combines_relative_and_weekday():
    today = date(2026, 6, 24)
    assert date_suffix_pt(date(2026, 6, 25), today) == "amanhã, quinta-feira"
    assert date_suffix_pt(date(2026, 7, 10), today) == "sexta-feira"


def test_format_date_pt():
    today = date(2026, 6, 24)
    assert format_date_pt(date(2026, 6, 25), today) == "25/06 (amanhã, quinta-feira)"
    assert format_date_pt(date(2026, 7, 10), today) == "10/07 (sexta-feira)"


def test_build_date_reference_structure():
    now = datetime(2026, 6, 24, 14, 30, tzinfo=TZ)
    block = build_date_reference(now)
    # Header line with current date + weekday
    assert "Data e hora atual (America/Recife): 24/06/2026 14:30 (quarta-feira)." in block
    assert "CALENDÁRIO DE REFERÊNCIA" in block
    # hoje / amanhã rows present and correctly labelled
    assert "hoje   = 24/06 (quarta-feira)" in block
    assert "amanhã = 25/06 (quinta-feira)" in block
    # Window covers today + REFERENCE_WINDOW_DAYS days (one row each)
    last_day = (now.date()).toordinal() + REFERENCE_WINDOW_DAYS
    from datetime import date as _date
    assert _date.fromordinal(last_day).strftime("%d/%m") in block


def test_build_date_reference_month_rollover():
    # 35 days ahead of 2026-06-24 is 2026-07-29
    now = datetime(2026, 6, 24, 9, 0, tzinfo=TZ)
    block = build_date_reference(now)
    assert "29/07 (quarta-feira)" in block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.dates'`

- [ ] **Step 3: Write `app/dates.py`**

```python
"""Deterministic calendar helpers — weekday and relative-day labels in pt-BR.

LLMs make calendar-arithmetic mistakes, so all weekday / hoje-amanhã reasoning
is computed here and injected into prompts as ready-made labels.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")

# How many days ahead the reference calendar block lists (besides today).
REFERENCE_WINDOW_DAYS = 35

_WEEKDAYS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


def weekday_pt(d: date) -> str:
    """Portuguese weekday name, e.g. 'terça-feira'."""
    return _WEEKDAYS_PT[d.weekday()]


def relative_label(target: date, today: date) -> str | None:
    """'hoje' / 'amanhã' / 'depois de amanhã' for near dates, else None."""
    delta = (target - today).days
    if delta == 0:
        return "hoje"
    if delta == 1:
        return "amanhã"
    if delta == 2:
        return "depois de amanhã"
    return None


def date_suffix_pt(target: date, today: date) -> str:
    """Parenthetical content: 'amanhã, quinta-feira' or just 'quinta-feira'."""
    rel = relative_label(target, today)
    wd = weekday_pt(target)
    return f"{rel}, {wd}" if rel else wd


def format_date_pt(target: date, today: date) -> str:
    """'25/06 (amanhã, quinta-feira)' or '10/07 (sexta-feira)'."""
    return f"{target.strftime('%d/%m')} ({date_suffix_pt(target, today)})"


def build_date_reference(now: datetime) -> str:
    """Reference block: current datetime header + a row per day for the next
    REFERENCE_WINDOW_DAYS days, each pre-labelled with its weekday."""
    today = now.date()
    lines = [
        f"Data e hora atual (America/Recife): "
        f"{now.strftime('%d/%m/%Y %H:%M')} ({weekday_pt(today)}).",
        "",
        "CALENDÁRIO DE REFERÊNCIA — use SEMPRE estes rótulos prontos. NUNCA calcule",
        "dia da semana nem hoje/amanhã por conta própria:",
    ]
    for i in range(REFERENCE_WINDOW_DAYS + 1):
        d = today + timedelta(days=i)
        if i == 0:
            prefix = "hoje   = "
        elif i == 1:
            prefix = "amanhã = "
        else:
            prefix = " " * 9
        lines.append(f"  {prefix}{d.strftime('%d/%m')} ({weekday_pt(d)})")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dates.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/dates.py tests/test_dates.py
git commit -m "feat: app/dates.py — deterministic pt-BR calendar helpers"
```

---

## Task 2: Ferramenta `consultar_data`

**Files:**
- Modify: `app/graph/tools.py` (adicionar tool ao final, antes de nenhuma ordem específica)
- Modify: `app/graph/nodes.py:26-33` (registrar em `TOOLS`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
async def test_consultar_data_full_date():
    from app.graph.tools import consultar_data
    # 2026-09-15 is a Tuesday
    result = await consultar_data.coroutine(data="15/09/2026")
    assert "15/09/2026" in result
    assert "terça-feira" in result


async def test_consultar_data_today_and_tomorrow():
    from app.graph.tools import consultar_data
    now = datetime.now(TZ)
    today_str = now.strftime("%d/%m/%Y")
    tomorrow_str = (now + __import__("datetime").timedelta(days=1)).strftime("%d/%m/%Y")
    assert "(hoje)" in await consultar_data.coroutine(data=today_str)
    assert "(amanhã)" in await consultar_data.coroutine(data=tomorrow_str)


async def test_consultar_data_dd_mm_infers_future_year():
    from app.graph.tools import consultar_data
    now = datetime.now(TZ)
    # A date far behind in the year should resolve to a future occurrence,
    # never to a past date.
    result = await consultar_data.coroutine(data="01/01")
    # The output year is today's year or next year, and the relative part is
    # a future "(em N dias)" or "(hoje)" — never "atrás".
    assert "atrás" not in result


async def test_consultar_data_invalid_input():
    from app.graph.tools import consultar_data
    result = await consultar_data.coroutine(data="banana")
    assert "dd/mm" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k consultar_data -v`
Expected: FAIL with `ImportError: cannot import name 'consultar_data'`

- [ ] **Step 3: Add the tool in `app/graph/tools.py`**

Add at the end of the file (after the last tool, e.g. after `transfer_to_human`):

```python
@tool
async def consultar_data(data: str) -> str:
    """Retorna o dia da semana e a relação com hoje (hoje/amanhã/em N dias) de uma
    data. Use SEMPRE que precisar mencionar o dia da semana de uma data que NÃO
    esteja no CALENDÁRIO DE REFERÊNCIA do prompt (ou seja, mais de 35 dias à
    frente). Aceita 'dd/mm' ou 'dd/mm/aaaa'. Nunca calcule o dia da semana você
    mesmo — chame esta ferramenta."""
    from app.dates import TZ, weekday_pt

    today = datetime.now(TZ).date()
    raw = (data or "").strip()

    parsed = None
    # Full date first; then dd/mm with year inference.
    try:
        parsed = datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        try:
            dm = datetime.strptime(raw, "%d/%m")
        except ValueError:
            dm = None
        if dm is not None:
            # Find the next year (starting at the current year) in which dd/mm is
            # a valid date on/after today — handles 29/02 and past dates.
            for offset in range(0, 8):
                try:
                    cand = dm.replace(year=today.year + offset).date()
                except ValueError:
                    continue  # e.g. 29/02 on a non-leap year
                if cand >= today:
                    parsed = cand
                    break

    if parsed is None:
        return (
            "Não consegui entender a data. Envie no formato dd/mm ou dd/mm/aaaa "
            "(ex: 15/09 ou 15/09/2026)."
        )

    wd = weekday_pt(parsed)
    article = "um" if wd in ("sábado", "domingo") else "uma"
    delta = (parsed - today).days
    if delta == 0:
        rel = "hoje"
    elif delta == 1:
        rel = "amanhã"
    elif delta > 0:
        rel = f"em {delta} dias"
    else:
        rel = f"há {abs(delta)} dias atrás"

    return f"{parsed.strftime('%d/%m/%Y')} é {article} {wd} ({rel})."
```

- [ ] **Step 4: Register the tool in `app/graph/nodes.py`**

In the import block `from app.graph.tools import (` (around `nodes.py:10`), add `consultar_data` to the imported names. Then in the `TOOLS` list (`nodes.py:26-33`) add `consultar_data`:

```python
TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment, mark_reschedule_in_progress,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data,
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -k consultar_data -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py app/graph/nodes.py tests/test_tools.py
git commit -m "feat: consultar_data tool for weekday/relative-day of any date"
```

---

## Task 3: Injetar bloco de referência e rótulos nas consultas (nodes + prompts header)

**Files:**
- Modify: `app/graph/nodes.py:1051-1053` (today = bloco de referência)
- Modify: `app/graph/nodes.py:1166` (rótulo na linha da consulta)
- Modify: `app/graph/prompts.py:630` e `:802` (header `{today}`)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_process_message.py`:

```python
async def test_patient_agent_prompt_has_reference_block_and_appointment_labels():
    """The system prompt built by patient_agent_node must contain the calendar
    reference block and pre-computed weekday/relative labels for appointments."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from unittest.mock import AsyncMock, MagicMock, patch
    from langchain_core.messages import HumanMessage, AIMessage
    from tests.conftest import CONFIG
    from app.graph.nodes import patient_agent_node

    TZ = ZoneInfo("America/Recife")
    now = _dt.datetime.now(TZ)
    tomorrow = (now + _dt.timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

    appt = {
        "appointment_id": "appt-1",
        "start_time": tomorrow.isoformat(),
        "booking_fee_paid_at": now.isoformat(),
        "booking_fee_waived": False,
        "recently_ended": False,
    }

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="ok")

    state = {
        "phone": "5581999999999@s.whatsapp.net",
        "stage": "patient_agent",
        "user_name": "Maria Silva",
        "patient_name": "Maria Silva",
        "patient_age": 30,
        "is_patient": True,
        "is_returning_patient": True,
        "preferred_doctor": "julio",
        "messages": [HumanMessage(content="oi")],
    }

    with patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[appt]), \
         patch("app.google_calendar.format_doctor_schedules", return_value=""), \
         patch("app.graph.nodes._get_agent_llm", return_value=_FakeLLM()), \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock):
        await patient_agent_node(state, CONFIG)

    system_prompt = captured["messages"][0].content
    assert "CALENDÁRIO DE REFERÊNCIA" in system_prompt
    assert "Consultas agendadas para este paciente:" in system_prompt
    # The appointment line carries the pre-computed relative + weekday label.
    assert "(amanhã," in system_prompt
```

> **Nota para o executor:** `patient_agent_node` toca várias dependências de IO. Os patches acima cobrem o caminho normal. Se ao rodar surgir uma chamada de rede/DB real (ex: `get_supabase`, `get_users_by_phone`), adicione o patch correspondente em `app.graph.nodes.<nome>` e rode de novo — não altere a asserção.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py -k reference_block -v`
Expected: FAIL — `assert "(amanhã," in system_prompt` (rótulo ainda não existe) ou `CALENDÁRIO DE REFERÊNCIA` ausente.

- [ ] **Step 3: Inject the reference block in `app/graph/nodes.py`**

Replace `nodes.py:1051-1053`:

```python
    _now_recife = datetime.now(ZoneInfo("America/Recife"))
    _weekday_pt = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"][_now_recife.weekday()]
    today = f"{_now_recife.strftime('%d/%m/%Y %H:%M')} ({_weekday_pt})"
```

with:

```python
    from app.dates import build_date_reference, date_suffix_pt
    _now_recife = datetime.now(ZoneInfo("America/Recife"))
    today = build_date_reference(_now_recife)
```

- [ ] **Step 4: Enrich the appointment line in `app/graph/nodes.py`**

Replace `nodes.py:1166`:

```python
            label = f"- {dt.strftime('%d/%m/%Y às %H:%M')} (ID: {apt['appointment_id']}){fee_tag}"
```

with:

```python
            _suffix = date_suffix_pt(dt.date(), _now_recife.date())
            label = f"- {dt.strftime('%d/%m/%Y às %H:%M')} ({_suffix}) (ID: {apt['appointment_id']}){fee_tag}"
```

- [ ] **Step 5: Fix the prompt header in `app/graph/prompts.py`**

The reference block already starts with its own "Data e hora atual..." line, so the template must not duplicate it. In **both** templates, replace the line:

```
Data e hora atual (America/Recife): {today}.
```

with just:

```
{today}
```

This occurs at `prompts.py:630` (EXISTING_PATIENT_SYSTEM) and `prompts.py:802` (NEW_PATIENT_SYSTEM). Use the Edit tool with `replace_all=True` since the two lines are identical.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_process_message.py -k reference_block -v`
Expected: PASS (adicione patches faltantes se necessário, conforme a nota do Step 1).

- [ ] **Step 7: Commit**

```bash
git add app/graph/nodes.py app/graph/prompts.py tests/test_process_message.py
git commit -m "feat: inject calendar reference block + labelled appointments into prompt"
```

---

## Task 4: Enxugar instruções de cálculo nos dois templates

**Files:**
- Modify: `app/graph/prompts.py` (linhas ~696/837 e ~739-743/899-903)

Não há teste novo: é remoção de instrução. A cobertura de comportamento vem dos rótulos já testados (Tasks 1-3). Ao final, roda-se a suíte inteira (Task 5).

- [ ] **Step 1: Substituir o bloco "NUNCA calcule ou infira o dia da semana"**

Esse item aparece idêntico nos dois templates (`prompts.py:696` e `:837`). Use a Edit tool com `replace_all=True`.

Texto atual (uma linha lógica, terminada em `\`):

```
- CRÍTICO — NUNCA calcule ou infira o dia da semana de uma data por conta própria (ex: "01/07 é segunda-feira"). LLMs cometem erros de calendário. O dia da semana só deve ser mencionado se vier explicitamente de uma resposta de get_available_slots ou se o próprio paciente/atendente informar. Se não tiver essa informação de uma fonte confiável, omita o dia da semana e use apenas a data (ex: "dia 01/07").
```

Substituir por:

```
- Para qualquer dia da semana ou relação hoje/amanhã, use SOMENTE os rótulos já prontos no CALENDÁRIO DE REFERÊNCIA (início do prompt) e ao lado de cada consulta em "Consultas agendadas". Para datas a mais de 35 dias à frente, chame a ferramenta consultar_data. NUNCA calcule dia da semana nem hoje/amanhã por conta própria — LLMs erram calendário.
```

> Confira no arquivo se a linha termina com `\` (continuação) — preserve o `\` final ao substituir, mantendo o mesmo estilo das linhas vizinhas.

- [ ] **Step 2: Substituir o bloco "Para saber se diz hoje ou amanhã"**

Esse item aparece idêntico nos dois templates (`prompts.py:739-743` e `:899-903`). Use a Edit tool com `replace_all=True`.

Texto atual (linhas terminadas em `\`):

```
Para saber se diz "hoje" ou "amanhã": compare a DATA da consulta (listada em "Consultas agendadas") \
com a DATA ATUAL (campo "Data e hora atual" no início do prompt). \
Se a data da consulta for IGUAL à data atual → use "hoje". \
Se a data da consulta for o dia seguinte → use "amanhã". \
NUNCA use "amanhã" quando a consulta for hoje, nem "hoje" quando for amanhã. \
```

Substituir por:

```
Para saber se diz "hoje" ou "amanhã", leia o rótulo já pronto ao lado da consulta \
em "Consultas agendadas" (ex: "(amanhã, quinta-feira)"). NUNCA calcule isso por conta própria. \
```

- [ ] **Step 3: Verify the templates still format**

Run: `uv run python -c "from app.graph.prompts import EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM; print('ok')"`
Expected: `ok` (sem erro de sintaxe / placeholder).

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "refactor: prompt usa rótulos prontos de data em vez de pedir cálculo ao LLM"
```

---

## Task 5: Verificação final da suíte

**Files:** nenhum (verificação).

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --tb=short`
Expected: todos os testes passam. Se algum teste pré-existente quebrar por causa do novo header do prompt (ex: asserção em `test_process_message`/`test_tools` que esperava `Data e hora atual (America/Recife): {today}`), atualize a asserção para o novo formato (bloco de referência) — não reverta a mudança de produção.

- [ ] **Step 2: Final commit (se houve ajustes de teste)**

```bash
git add -A
git commit -m "test: ajustar asserções afetadas pelo bloco de referência de data"
```

---

## Self-Review (preenchido pelo autor do plano)

**Spec coverage:**
- `app/dates.py` (módulo isolado) → Task 1 ✓
- Bloco de referência de 35 dias → Task 1 (`build_date_reference`) + Task 3 (injeção) ✓
- Rótulos nas consultas agendadas → Task 3 (Step 4) ✓
- Ferramenta `consultar_data` (datas distantes) → Task 2 ✓
- Enxugar instruções de cálculo → Task 4 ✓
- Testes: `test_dates.py` (Task 1), `test_tools.py` (Task 2), `test_process_message.py` (Task 3) ✓
- Tratamento de erro da tool (entrada inválida) → Task 2 (Step 1 test + impl) ✓
- Inferência de ano dd/mm + 29/02 → Task 2 (loop de offset) ✓

**Type consistency:** `weekday_pt`, `relative_label`, `date_suffix_pt`, `format_date_pt`, `build_date_reference`, `REFERENCE_WINDOW_DAYS`, `TZ` — nomes idênticos entre Task 1 (definição), Task 2 (uso na tool) e Task 3 (uso em nodes). `consultar_data` idêntico entre Tasks 2 e 4. ✓

**Placeholder scan:** sem TBD/TODO; todo passo de código mostra o código. A nota do Task 3 sobre patches adicionais é orientação de execução de teste (TDD normal), não um placeholder de produção. ✓
