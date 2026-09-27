# Painel da Eva, Parte 2: lista de consultas e nova consulta — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A atendente vê as consultas futuras do paciente numa aba Consultas e cria consultas pelo painel (encaixe com confirmação azul, 1ª consulta infantil dividida, cortesia ou taxa isenta por consulta), com a mensagem ao paciente entrando na memória da Eva.

**Architecture:** O painel (`dashboard/`, que não importa `app/`) lê as consultas direto do Supabase e, para criar, chama um endpoint interno novo da Eva (`POST /admin/panel/appointments`, cabeçalho `X-Admin-Secret`). A Eva faz checagem de grade/conflito, cria evento no Calendar, grava `appointments`, avisa a clínica, manda a mensagem aos destinatários da regra da idade e grava a mensagem em `messages` e no checkpoint do LangGraph. Uma coluna nova `appointments.is_courtesy` marca cortesia por consulta, e `appointments.session_note` guarda a observação (inclusive "parte 1 de 2").

**Tech Stack:** FastAPI, postgrest-py async (Supabase), Google Calendar API, LangGraph checkpoint, Jinja2 + JS puro no painel, pytest/pytest-asyncio, httpx.

**Spec:** `docs/superpowers/specs/2026-09-26-painel-vinculo-e-agendamento-design.md` (seções Interface, Parte 2, Mensagens, Erros).

---

## Regras de trabalho

- Worktree: `/Users/ayexatavares/Projetos/psique-ai/.worktrees/painel-consultas`, branch `painel-consultas`. Nunca trabalhar na main.
- Testes da Eva: `uv run pytest --tb=short -q` na raiz da worktree. Nunca passar `tests/test_patients.py` como primeiro argumento do pytest (import circular); para rodar só ele, use `uv run pytest tests/test_tools.py tests/test_patients.py -q`.
- Testes do painel: `cd dashboard && uv run pytest -q`.
- O painel não pode importar nada de `app/`. Constantes compartilhadas (rótulos de parte da 1ª consulta, ids de médico) são duplicadas de propósito, com comentário apontando a outra cópia.
- Datas: o painel manda horário local de Recife sem fuso (`"2026-10-05T14:00"`). A Eva faz `datetime.fromisoformat(s).replace(tzinfo=TZ)`. Leituras de `start_time` sem fuso são UTC.
- Commits pequenos, mensagem em português no padrão `feat(painel): ...` / `fix(cortesia): ...`, terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Ordem de deploy (importante)

A migração (Task 1) precisa ser aplicada em produção **antes** do merge, porque várias consultas passam a selecionar `is_courtesy` e `session_note`. Selecionar coluna inexistente derruba cron e painel. A Task 15 cobre isso.

## Mapa de arquivos

Eva (raiz):
- Create `supabase/migrations/20260927_appointments_courtesy_session_note.sql`
- Create `app/booking_texts.py`: frases de confirmação e linha "Dr. Júlio — segunda-feira, 05/10/2026 às 14:00".
- Modify `app/graph/nodes.py` (~2348-2376): usa `booking_texts`.
- Modify `app/google_calendar.py`: nova `grid_violation(doctor, start, minutes)`.
- Modify `app/graph/tools.py` (~1055-1122): `confirm_appointment` usa `grid_violation`.
- Create `app/panel_booking.py`: checagem de encaixe, criação, mensagem.
- Modify `app/main.py`: `POST /admin/panel/appointments`.
- Modify `scripts/send_payment_reminders.py`, `scripts/send_pending_payments_reminder.py`, `app/patient_attributes.py`, `app/database.py`: cortesia por consulta.
- Tests: `tests/test_booking_texts.py` (novo), `tests/test_calendar.py`, `tests/test_panel_booking.py` (novo), `tests/test_webhook.py`, `tests/test_payment_reminders_cancel.py`, `tests/test_patient_attributes.py`, `tests/test_tools.py`.

Painel (`dashboard/`):
- Modify `pyproject.toml`: `httpx` vira dependência de runtime.
- Create `dashboard/eva_client.py`: chamada à Eva com `X-Admin-Secret`.
- Modify `dashboard/attendant_db.py`: `list_consultas`, `set_first_consultation`.
- Modify `dashboard/attendant_routes.py`: `GET /consultas`, `POST /consulta/{appointment_id}/primeira`, `POST /consulta/nova`.
- Modify `dashboard/payments.py`: `compute_pendencias` ignora cortesia.
- Modify `dashboard/templates/atendente.html`: aba Consultas e folha "Nova consulta".
- Tests: `dashboard/tests/test_eva_client.py` (novo), `test_attendant_db.py`, `test_attendant_routes.py`, `test_attendant_scope.py`, `test_payments.py`.

---

### Task 1: Migração `is_courtesy` e `session_note`

**Files:**
- Create: `supabase/migrations/20260927_appointments_courtesy_session_note.sql`

- [ ] **Step 1: Escrever a migração**

```sql
-- Painel da Eva, Parte 2 (27/09/2026).
-- is_courtesy: cortesia vale só para esta consulta (antes só existia
-- patients.custom_price = 0, que vale para todas). Uma consulta é cortesia se
-- esta coluna for verdadeira OU se a ficha tiver custom_price = 0.
-- session_note: observação da sessão (ex.: "Domiciliar",
-- "1ª consulta · parte 1 de 2"). Antes ficava só no título do evento do Calendar;
-- o painel precisa dela para saber que falta marcar a 2ª parte.

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS is_courtesy BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS session_note TEXT NULL;
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/20260927_appointments_courtesy_session_note.sql
git commit -m "feat(db): appointments.is_courtesy e session_note (migração)"
```

---

### Task 2: Textos de confirmação numa fonte única

Hoje as frases que o paciente recebe ao agendar estão dentro de `nodes.py`. O painel precisa das mesmas frases. Extrair sem mudar uma vírgula.

**Files:**
- Create: `app/booking_texts.py`
- Modify: `app/graph/nodes.py` (bloco que começa em `if _result_body.startswith("AGENDAMENTO_CORTESIA\n"):`, ~2362-2376)
- Test: `tests/test_booking_texts.py`

- [ ] **Step 1: Teste que falha**

```python
# tests/test_booking_texts.py
from datetime import datetime
from zoneinfo import ZoneInfo

from app.booking_texts import confirmation_text, format_appt_line

TZ = ZoneInfo("America/Recife")


def test_format_appt_line_with_and_without_note():
    start = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
    assert format_appt_line("julio", start) == "Dr. Júlio — segunda-feira, 05/10/2026 às 14:00"
    assert format_appt_line("bruna", start, "Domiciliar") == (
        "Dra. Bruna — segunda-feira, 05/10/2026 às 14:00 (Domiciliar)"
    )


def test_confirmation_text_normal_has_pix_and_2h():
    txt = confirmation_text("normal", "Dr. Júlio — segunda-feira, 05/10/2026 às 14:00", "Carla")
    assert txt.startswith("Consulta registrada! ✅\nDr. Júlio — segunda-feira, 05/10/2026 às 14:00\n\n")
    assert "R$ 100,00 em até 2 horas" in txt
    assert "💳 PIX: 42006848000178" in txt


def test_confirmation_text_variants():
    line = "Dr. Júlio — x"
    assert confirmation_text("cortesia", line, "Carla") == (
        "Perfeito, Carla! 😊 Consulta confirmada:\nDr. Júlio — x\n\n"
        "Como combinado, a taxa de reserva está isenta. Até lá!"
    )
    assert confirmation_text("taxa_isenta", line, "Carla").endswith("A taxa de reserva foi dispensada. Até lá!")
    assert confirmation_text("taxa_paga", line, "Carla").endswith("A taxa de reserva já está paga. Até lá!")


def test_confirmation_text_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        confirmation_text("xyz", "l", "c")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_booking_texts.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.booking_texts'`.

- [ ] **Step 3: Implementar**

```python
# app/booking_texts.py
"""Frases de confirmação de agendamento enviadas ao paciente.

Fonte única usada pelo fast-path do grafo (app/graph/nodes.py) e pelo painel da
atendente (app/panel_booking.py). Mudar uma frase aqui muda nos dois lugares.
"""
from datetime import datetime

from app.graph.prompts import CORRECT_PIX_KEY

DOCTOR_LABELS = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}
_WEEKDAYS = {
    0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
    3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo",
}


def format_appt_line(doctor: str, start: datetime, session_note: str = "") -> str:
    """'Dr. Júlio — segunda-feira, 05/10/2026 às 14:00 (nota)'. `start` já em Recife."""
    label = DOCTOR_LABELS.get(doctor, doctor)
    when = f"{_WEEKDAYS[start.weekday()]}, {start.strftime('%d/%m/%Y às %H:%M')}"
    note = f" ({session_note})" if session_note else ""
    return f"{label} — {when}{note}"


def confirmation_text(kind: str, appt_line: str, contact_name: str) -> str:
    """kind: normal | taxa_isenta | cortesia | taxa_paga."""
    if kind == "cortesia":
        return (
            f"Perfeito, {contact_name}! 😊 Consulta confirmada:\n{appt_line}\n\n"
            f"Como combinado, a taxa de reserva está isenta. Até lá!"
        )
    if kind == "taxa_isenta":
        return (
            f"Perfeito, {contact_name}! 😊 Consulta confirmada:\n{appt_line}\n\n"
            f"A taxa de reserva foi dispensada. Até lá!"
        )
    if kind == "taxa_paga":
        return (
            f"Perfeito, {contact_name}! 😊 Consulta confirmada:\n{appt_line}\n\n"
            f"A taxa de reserva já está paga. Até lá!"
        )
    if kind == "normal":
        return (
            f"Consulta registrada! ✅\n{appt_line}\n\n"
            f"Para garantir a vaga, é necessário o pagamento da taxa de reserva de R$ 100,00 em até 2 horas.\n"
            f"💳 PIX: {CORRECT_PIX_KEY}\n\n"
            f"Esse valor será abatido do total da consulta. Em caso de cancelamento ou remarcação com menos de 24h de antecedência ou ausência sem justificativa, a taxa não é devolvida."
        )
    raise ValueError(f"kind desconhecido: {kind}")
```

Em `app/graph/nodes.py`, trocar o bloco `if _result_body.startswith("AGENDAMENTO_CORTESIA\n"): ... else: ... _patient_msg = (...)` por:

```python
                from app.booking_texts import confirmation_text as _confirmation_text
                if _result_body.startswith("AGENDAMENTO_CORTESIA\n"):
                    _kind = "cortesia"
                elif _result_body.startswith("AGENDAMENTO_TAXA_DISPENSADA\n"):
                    _kind = "taxa_isenta"
                else:
                    _kind = "normal"
                _patient_msg = _confirmation_text(_kind, _appt_line, _contact_name)
```

Se `CORRECT_PIX_KEY` ficar sem uso em `nodes.py`, remover o import.

- [ ] **Step 4: Rodar**

Run: `uv run pytest tests/test_booking_texts.py tests/test_process_message.py -q`
Expected: PASS (os testes de `test_process_message.py` que conferem o texto em ~1584, 4285, 5975 continuam passando sem mudança).

- [ ] **Step 5: Commit**

```bash
git add app/booking_texts.py app/graph/nodes.py tests/test_booking_texts.py
git commit -m "refactor(agendamento): frases de confirmação numa fonte única"
```

---

### Task 3: `grid_violation` extraída do `confirm_appointment`

A checagem de grade vive inline no `confirm_appointment`. O painel precisa da mesma regra. Extrair para `app/google_calendar.py`, devolvendo um código; o `confirm_appointment` continua devolvendo exatamente os mesmos textos.

**Files:**
- Modify: `app/google_calendar.py` (adicionar depois de `merge_adjacent_windows`)
- Modify: `app/graph/tools.py` (bloco `if not force_encaixe:` da checagem de grade, ~1055-1122)
- Test: `tests/test_calendar.py`

- [ ] **Step 1: Testes que falham**

Acrescentar no fim de `tests/test_calendar.py`:

```python
# ── grid_violation ─────────────────────────────────────────────────────────────
from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _ZI
from unittest.mock import patch as _patch

from app.google_calendar import grid_violation

_TZR = _ZI("America/Recife")


def test_grid_violation_ok_inside_window():
    # segunda 09:00 Dr. Júlio (grade seg 9-12)
    assert grid_violation("julio", _dt(2026, 10, 5, 9, 0, tzinfo=_TZR), 60) is None


def test_grid_violation_weekday_off():
    # terça: Dr. Júlio não atende
    assert grid_violation("julio", _dt(2026, 10, 6, 9, 0, tzinfo=_TZR), 60) == "dia_sem_atendimento"


def test_grid_violation_outside_grid():
    assert grid_violation("julio", _dt(2026, 10, 5, 13, 0, tzinfo=_TZR), 60) == "fora_da_grade"


def test_grid_violation_julio_block_overruns_day():
    # quinta fecha 20:00; 2h a partir das 19:00 estoura
    assert grid_violation("julio", _dt(2026, 10, 8, 19, 0, tzinfo=_TZR), 120) == "estoura_expediente"


def test_grid_violation_blocked_day():
    with _patch.dict("app.google_calendar.SCHEDULE_EXCEPTIONS", {"julio": {"2026-10-05": []}}):
        assert grid_violation("julio", _dt(2026, 10, 5, 9, 0, tzinfo=_TZR), 60) == "dia_bloqueado"


def test_grid_violation_exception_window():
    exc = {"julio": {"2026-10-05": [(15, 0, 17, 0, "escolha")]}}
    with _patch.dict("app.google_calendar.SCHEDULE_EXCEPTIONS", exc):
        assert grid_violation("julio", _dt(2026, 10, 5, 9, 0, tzinfo=_TZR), 60) == "fora_da_excecao"
        assert grid_violation("julio", _dt(2026, 10, 5, 15, 0, tzinfo=_TZR), 60) is None
```

Antes de escrever, confirmar em `DOCTOR_SCHEDULES` que segunda 9-12 e quinta até 20h valem para o Dr. Júlio (`app/google_calendar.py:77-88`) e ajustar as datas se a grade tiver mudado.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_calendar.py -q -k grid_violation`
Expected: FAIL, `ImportError: cannot import name 'grid_violation'`.

- [ ] **Step 3: Implementar em `app/google_calendar.py`**

```python
def grid_violation(doctor: str, start: datetime, slot_minutes: int) -> str | None:
    """Confere `start` (já em Recife) contra a grade do médico.

    Devolve None se o horário cabe, ou um código:
    dia_bloqueado | fora_da_excecao | dia_sem_atendimento | fora_da_grade | estoura_expediente.
    Fonte única da regra usada por confirm_appointment e pelo painel da atendente.
    """
    exc_map = SCHEDULE_EXCEPTIONS.get(doctor, {})
    date_key = start.date().isoformat()
    slot_min = start.hour * 60 + start.minute

    if date_key in exc_map:
        day_wins = exc_map[date_key]
        if not day_wins:
            return "dia_bloqueado"
        if not any((sh * 60 + sm) <= slot_min < (eh * 60 + em) for sh, sm, eh, em, _ in day_wins):
            return "fora_da_excecao"
    else:
        day_wins = DOCTOR_SCHEDULES.get(doctor, {}).get(start.weekday())
        if day_wins is None:
            return "dia_sem_atendimento"
        if not any((sh * 60 + sm) <= slot_min < (eh * 60 + em) for sh, sm, eh, em, _ in day_wins):
            return "fora_da_grade"

    # Dr. Júlio: o bloco inteiro precisa caber numa janela (caso Bernardo/Mônica, 09/07/2026).
    if doctor == "julio":
        end_min = slot_min + slot_minutes
        if not any(
            (sh * 60 + sm) <= slot_min and end_min <= (eh * 60 + em)
            for sh, sm, eh, em, _ in merge_adjacent_windows(day_wins)
        ):
            return "estoura_expediente"
    return None
```

Observação: `grid_violation` não usa `_get_doctor_schedule`/`SCHEDULE_CHANGES`, igual ao código atual do `confirm_appointment`. Não mudar isso aqui.

- [ ] **Step 4: Trocar o bloco inline do `confirm_appointment`**

Substituir o bloco inteiro `if not force_encaixe:` da checagem de grade (do `from app.google_calendar import SCHEDULE_EXCEPTIONS, DOCTOR_SCHEDULES` até o fim do `if doctor == "julio":`) por:

```python
    # Reject slots outside the doctor's schedule — skipped for encaixe
    if not force_encaixe:
        from app.google_calendar import grid_violation
        _code = grid_violation(doctor, start, slot_duration_minutes)
        if _code:
            _doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
            _day = start.strftime("%d/%m/%Y")
            _retry = "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
            _prefix = "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
            if _code == "dia_bloqueado":
                return f"{_prefix}{_doctor_label} não tem atendimento no dia {_day}. {_retry}"
            if _code == "fora_da_excecao":
                return f"{_prefix}Este horário não está dentro da disponibilidade de {_doctor_label} no dia {_day}. {_retry}"
            if _code == "dia_sem_atendimento":
                _day_name = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
                             3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"}[start.weekday()]
                return f"{_prefix}{_doctor_label} não atende {_day_name}. {_retry}"
            if _code == "fora_da_grade":
                return (f"{_prefix}Este horário ({start.strftime('%H:%M')}) está fora da grade de "
                        f"atendimento de {_doctor_label}. {_retry}")
            _dur_h = slot_duration_minutes // 60
            return (
                f"{_prefix}Não há bloco de {_dur_h}h seguidas a partir das {start.strftime('%H:%M')} na grade de "
                f"{_doctor_label} no dia {_day} — o horário ultrapassaria o fim do expediente. "
                "Chame get_available_slots novamente para um horário que comporte a duração, ou ofereça "
                "agendar os dois momentos da 1ª consulta em sessões separadas de 1h."
            )
```

Se algum código abaixo desse bloco usar `_day_wins` ou `_doctor_label` definidos lá, conferir com `grep -n "_day_wins\|_doctor_label" app/graph/tools.py` e redefinir localmente onde for preciso.

- [ ] **Step 5: Rodar**

Run: `uv run pytest tests/test_calendar.py tests/test_tools.py -q`
Expected: PASS. Os testes de grade e de estouro do Dr. Júlio em `test_tools.py` (~2055-2121) seguem verdes, provando que os textos não mudaram.

- [ ] **Step 6: Commit**

```bash
git add app/google_calendar.py app/graph/tools.py tests/test_calendar.py
git commit -m "refactor(agenda): grid_violation como fonte única da checagem de grade"
```

---

### Task 4: Cortesia por consulta nos crons e atributos

Uma consulta é cortesia se `appointments.is_courtesy` for verdadeiro **ou** `patients.custom_price == 0`. Cada ponto ganha teste, porque já tivemos cortesia sendo cobrada (caso Lucas Raphael).

**Files:**
- Modify: `scripts/send_payment_reminders.py` (`_is_courtesy` ~431, `_appt_select` ~657)
- Modify: `scripts/send_pending_payments_reminder.py` (consultas r1 ~69-78 e r2 ~81-94)
- Modify: `app/patient_attributes.py` (`fee_status` ~40)
- Modify: `app/database.py` (`_appt_fields` ~458)
- Test: `tests/test_payment_reminders_cancel.py`, `tests/test_patient_attributes.py`, `tests/test_tools.py` (`test_pending_payments_courtesy_filter` ~6114)

- [ ] **Step 1: Testes que falham**

Em `tests/test_payment_reminders_cancel.py`, logo depois de `test_cancel_skipped_for_courtesy_patient`:

```python
@pytest.mark.asyncio
async def test_reminder_skipped_for_courtesy_appointment():
    """Cortesia só desta consulta (appointments.is_courtesy), ficha com preço normal."""
    client, table = _client()
    now = datetime(2026, 8, 11, 12, 55, tzinfo=TZ)
    appt = _appt(is_courtesy=True, patients={"name": "Ana", "custom_price": None})
    with patch("scripts.send_payment_reminders.get_financial_contacts", new_callable=AsyncMock) as mock_contacts, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp:
        await spr._send_payment_reminder(client, appt, None, now)
    mock_contacts.assert_not_awaited()
    mock_wpp.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_skipped_for_courtesy_appointment():
    client, table = _client()
    now = datetime(2026, 8, 11, 15, 0, tzinfo=TZ)
    appt = _appt(is_courtesy=True, patients={"name": "Ana", "custom_price": None})
    with patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cancel, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)
    mock_cancel.assert_not_awaited()
    table.update.assert_not_called()


def test_appt_select_includes_is_courtesy():
    import inspect
    assert "is_courtesy" in inspect.getsource(spr.main)
```

Conferir a assinatura de `_appt(**overrides)` no topo do arquivo (~11-20); se não aceitar chaves extras, acrescentar `**kw` que faz `d.update(kw)`.

Em `tests/test_patient_attributes.py`:

```python
def test_fee_status_courtesy_appointment():
    appt = _appt(NOW)
    appt["is_courtesy"] = True
    assert fee_status(appt, None) == "Isenta"
```

Em `tests/test_tools.py`, trocar o corpo de `test_pending_payments_courtesy_filter` para chamar os filtros reais que serão criados no script:

```python
def test_pending_payments_courtesy_filter():
    from scripts.send_pending_payments_reminder import _not_courtesy
    rows = [
        {"patients": {"custom_price": 0}},
        {"is_courtesy": True, "patients": {"custom_price": 300}},
        {"patients": {"custom_price": None}},
    ]
    assert [_not_courtesy(r) for r in rows] == [False, False, True]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py tests/test_patient_attributes.py tests/test_tools.py -q -k courtesy`
Expected: FAIL nos testes novos.

- [ ] **Step 3: Implementar**

`scripts/send_payment_reminders.py`:

```python
def _is_courtesy(appt: dict) -> bool:
    """Cortesia não deve taxa nenhuma. Vale a da consulta (appointments.is_courtesy,
    marcada pelo painel) ou a da ficha (patients.custom_price == 0, caso Lucas
    Raphael 5581973460726, 11/08/2026). Diferente de booking_fee_waived, que isenta
    só a taxa de reserva."""
    if appt.get("is_courtesy"):
        return True
    return (appt.get("patients") or {}).get("custom_price") == 0
```

e em `main()`:

```python
    _appt_select = ("appointment_id, start_time, doctor_id, created_at, payment_reminder_sent_at, "
                    "contact_id, patient_id, is_courtesy, patients(name, custom_price)")
```

`scripts/send_pending_payments_reminder.py`: adicionar no topo do módulo

```python
def _not_courtesy(appt: dict) -> bool:
    """False para cortesia: da consulta (is_courtesy) ou da ficha (custom_price == 0)."""
    if appt.get("is_courtesy"):
        return False
    return (appt.get("patients") or {}).get("custom_price") != 0
```

Incluir `is_courtesy` no select das duas consultas (r1 e r2). Na r1 (taxa), que hoje não exclui cortesia, filtrar a lista com `_not_courtesy`. Na r2, trocar a list comprehension atual de `custom_price != 0` por `_not_courtesy`.

`app/patient_attributes.py`:

```python
def fee_status(next_appt: dict, custom_price) -> str:
    if next_appt.get("booking_fee_waived") or next_appt.get("is_courtesy") or custom_price == 0:
        return "Isenta"
```

(o restante da função fica igual).

`app/database.py`, em `get_upcoming_appointments`: acrescentar `, is_courtesy` ao fim de `_appt_fields`.

- [ ] **Step 4: Rodar tudo**

Run: `uv run pytest --tb=short -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py scripts/send_pending_payments_reminder.py app/patient_attributes.py app/database.py tests/
git commit -m "fix(cortesia): cortesia por consulta (is_courtesy) nos crons e atributos; lembrete de taxa pendente deixa de cobrar cortesia"
```

---

### Task 5: Painel, pendências ignoram cortesia

Hoje `compute_pendencias` mostra "taxa R$100" e "consulta R$0" para paciente cortesia.

**Files:**
- Modify: `dashboard/payments.py` (`compute_pendencias`, select ~518-523 e laços ~577 e ~593)
- Test: `dashboard/tests/test_payments.py`

- [ ] **Step 1: Teste que falha**

Ler o fixture de loja fake usado pelos testes de `compute_pendencias` em `dashboard/tests/test_payments.py` (~15-30) e seguir o mesmo formato. Teste:

```python
@pytest.mark.asyncio
async def test_compute_pendencias_ignores_courtesy(fake_client):
    base = {"doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be", "status": "scheduled",
            "paid_at": None, "booking_fee_paid_at": None, "booking_fee_waived": False,
            "consultation_type": None, "start_time": "2026-10-05T17:00:00+00:00"}
    fake_client.store["appointments"] = [
        {**base, "appointment_id": "a1", "patient_id": "p1", "is_courtesy": True,
         "patients": {"name": "Ana", "birth_date": "01/01/1990", "custom_price": None, "patient_contacts": []}},
        {**base, "appointment_id": "a2", "patient_id": "p2", "is_courtesy": False,
         "patients": {"name": "Bia", "birth_date": "01/01/1990", "custom_price": 0, "patient_contacts": []}},
    ]
    result = await payments.compute_pendencias(fake_client, ["p1", "p2"])
    assert result == [] or all(not item.get("pendencias") for item in result)
```

Ajustar a última linha ao formato real que `compute_pendencias` devolve (ler a função antes; o teste deve afirmar "nenhuma pendência para a1 nem a2").

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_payments.py -q -k courtesy`
Expected: FAIL (hoje aparecem pendências).

- [ ] **Step 3: Implementar**

Acrescentar `is_courtesy` ao select, e logo depois de buscar as linhas (antes de agrupar por paciente) descartar cortesia:

```python
    rows = [
        r for r in rows
        if not r.get("is_courtesy") and (r.get("patients") or {}).get("custom_price") != 0
    ]
```

Usar o nome real da variável da lista na função.

- [ ] **Step 4: Rodar**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/payments.py dashboard/tests/test_payments.py
git commit -m "fix(painel): pendências não cobram consulta cortesia"
```

---

### Task 6: `panel_booking.check_slot`, motivo do encaixe

**Files:**
- Create: `app/panel_booking.py`
- Test: `tests/test_panel_booking.py`

- [ ] **Step 1: Testes que falham**

```python
# tests/test_panel_booking.py
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import panel_booking as pb

TZ = ZoneInfo("America/Recife")
MON_9 = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


def _sb(rows=None):
    """Cliente Supabase encadeável que devolve `rows` em qualquer select."""
    client = MagicMock()
    q = MagicMock()
    for m in ("select", "eq", "neq", "in_", "lt", "gt", "gte", "order", "limit", "insert", "update"):
        getattr(q, m).return_value = q
    q.execute = AsyncMock(return_value=MagicMock(data=rows or []))
    client.from_.return_value = q
    return client, q


@pytest.mark.asyncio
async def test_check_slot_free():
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        assert await pb.check_slot("julio", MON_9, 60, patient_id="p1") == []


@pytest.mark.asyncio
async def test_check_slot_outside_grid_reason():
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", datetime(2026, 10, 5, 13, 0, tzinfo=TZ), 60, patient_id="p1")
    assert reasons == ["fora do horário de atendimento"]


@pytest.mark.asyncio
async def test_check_slot_clash_names_other_patient():
    clash = [{"patient_id": "p9", "start_time": "2026-10-05T12:00:00+00:00", "patients": {"name": "Rafael Lima"}}]
    client, _ = _sb(clash)
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["bate com a consulta de Rafael às 09:00"]


@pytest.mark.asyncio
async def test_check_slot_calendar_busy():
    client, _ = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock,
               return_value=[{"start": "2026-10-05T09:00:00-03:00"}]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    assert reasons == ["agenda do médico ocupada às 09:00"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: FAIL, módulo inexistente.

- [ ] **Step 3: Implementar o começo de `app/panel_booking.py`**

```python
"""Agendamento feito pela atendente no painel (endpoint /admin/panel/appointments).

Reaproveita as regras da Eva: grade (grid_violation), textos (booking_texts),
destinatários pela regra da idade (consultation_reminder_contacts). A mensagem
enviada entra em `messages` e no checkpoint da conversa, para a Eva saber do que
se trata quando o paciente responder.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.database import DOCTOR_IDS, get_supabase
from app.google_calendar import grid_violation
from app.utils import display_name

_logger = logging.getLogger(__name__)
TZ = ZoneInfo("America/Recife")

# Mesmos textos em dashboard/templates/atendente.html (SPLIT_PART1/SPLIT_PART2).
SPLIT_PART1 = "1ª consulta · parte 1 de 2"
SPLIT_PART2 = "1ª consulta · parte 2 de 2"

_GRID_REASONS = {
    "dia_bloqueado": "dia bloqueado na agenda",
    "fora_da_excecao": "fora do horário de atendimento",
    "dia_sem_atendimento": "o médico não atende neste dia da semana",
    "fora_da_grade": "fora do horário de atendimento",
    "estoura_expediente": "passa do fim do expediente",
}


def _to_local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


async def _calendar_busy(doctor: str, start: datetime, end: datetime) -> list[dict]:
    """Eventos da Eva no Calendar do médico entre start e end. Erro = lista vazia
    (a checagem no banco continua valendo)."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import _credentials, _get_busy
    from googleapiclient.discovery import build
    try:
        calendar_id = await _get_doctor_calendar_id(doctor)
        service = build("calendar", "v3", credentials=_credentials())
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _get_busy, service, calendar_id, start, end)
    except Exception:
        _logger.exception("panel check_slot: falha ao ler o Calendar doctor=%s", doctor)
        return []


async def check_slot(doctor: str, start: datetime, minutes: int, patient_id: str) -> list[str]:
    """Motivos pelos quais o horário é encaixe. Lista vazia = horário livre na grade.

    Consultas do próprio paciente não contam como conflito (a 2ª parte da 1ª
    consulta infantil pode encostar na 1ª)."""
    reasons: list[str] = []
    code = grid_violation(doctor, start, minutes)
    if code:
        reasons.append(_GRID_REASONS[code])

    end = start + timedelta(minutes=minutes)
    client = await get_supabase()
    res = await (
        client.from_("appointments")
        .select("patient_id, start_time, patients(name)")
        .eq("doctor_id", DOCTOR_IDS[doctor])
        .eq("status", "scheduled")
        .lt("start_time", end.isoformat())
        .gt("end_time", start.isoformat())
        .execute()
    )
    clash_starts = set()
    for row in res.data or []:
        if row.get("patient_id") == patient_id:
            continue
        other = display_name((row.get("patients") or {}).get("name") or "") or "outro paciente"
        hhmm = _to_local(row["start_time"]).strftime("%H:%M")
        clash_starts.add(hhmm)
        reasons.append(f"bate com a consulta de {other} às {hhmm}")

    # Calendar pega também o que foi marcado à mão pela clínica (evento "Consulta ...").
    for ev in await _calendar_busy(doctor, start, end):
        hhmm = _to_local(ev["start"]).strftime("%H:%M")
        if hhmm not in clash_starts:
            reasons.append(f"agenda do médico ocupada às {hhmm}")
            clash_starts.add(hhmm)
    return reasons
```

Conferir que `_get_busy` devolve dicts com a chave `"start"` em ISO (ler `app/google_calendar.py:542-587`); se devolver datetimes, adaptar `_to_local`.

- [ ] **Step 4: Rodar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "feat(painel): check_slot explica por que o horário é encaixe"
```

---

### Task 7: `panel_booking.create_appointments`, gravação

Cria uma consulta, ou as duas partes da 1ª consulta infantil dividida, ou completa uma 2ª parte pendente.

**Contrato de entrada** (dict já validado pelo endpoint, Task 9):

```python
{
  "phone": "5581999998888@s.whatsapp.net",
  "contact_id": "c1",               # contato da conversa = quem agendou
  "patient": {...},                 # linha de patients
  "doctor": "julio" | "bruna",
  "modality": "online" | "presencial",
  "parts": [{"start": datetime, "minutes": 60}],   # 1 ou 2 itens; 2 só se split
  "split": bool,                    # 1ª consulta infantil em dois momentos
  "split_of": dict | None,          # linha de appointments da parte 1 pendente
  "session_note": "",               # observação livre (ex.: "Domiciliar")
  "first_consultation": bool,
  "billing": "normal" | "taxa_isenta" | "cortesia",
  "encaixe": bool,
  "agent": "Maria",
}
```

**Files:**
- Modify: `app/panel_booking.py`
- Test: `tests/test_panel_booking.py`

- [ ] **Step 1: Testes que falham**

```python
def _req(**kw):
    base = {
        "phone": "5581999998888@s.whatsapp.net", "contact_id": "c1",
        "patient": {"id": "p1", "name": "LUCAS MENEZES", "social_name": None, "email": "l@x.com",
                    "birth_date": "10/02/2016", "custom_price": None, "booking_fee_waived": False},
        "doctor": "julio", "modality": "presencial",
        "parts": [{"start": MON_9, "minutes": 60}], "split": False, "split_of": None,
        "session_note": "", "first_consultation": False, "billing": "normal",
        "encaixe": False, "agent": "Maria",
    }
    base.update(kw)
    return base


def _patches(client, event_ids=("evt1", "evt2")):
    return [
        patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client),
        patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"),
        patch("app.google_calendar.create_event", new_callable=AsyncMock, side_effect=list(event_ids)),
        patch("app.google_calendar.cancel_event", new_callable=AsyncMock),
        patch("app.panel_booking.log_event", new_callable=AsyncMock),
        patch("app.panel_booking._notify_clinic_async"),
    ]


async def _run(req, client, **kw):
    from contextlib import ExitStack
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client, **kw)]
        out = await pb.create_appointments(req)
    return out, mocks


@pytest.mark.asyncio
async def test_create_single_normal():
    client, q = _sb([])
    out, mocks = await _run(_req(), client)
    row = q.insert.call_args[0][0]
    assert row["appointment_id"] == "evt1"
    assert row["contact_id"] == "c1"
    assert row["patient_id"] == "p1"
    assert row["status"] == "scheduled"
    assert row["booking_fee_waived"] is False and row["booking_fee_paid_at"] is None
    assert row["is_courtesy"] is False
    assert row["consultation_type"] is None
    assert out["kind"] == "normal"
    ev = mocks[4].call_args
    assert ev[0][0] == "appointment_booked"
    assert ev[0][2]["origem"] == "painel" and ev[0][2]["atendente"] == "Maria" and ev[0][2]["encaixe"] is False


@pytest.mark.asyncio
async def test_create_courtesy_marks_fee_waived_too():
    client, q = _sb([])
    out, _ = await _run(_req(billing="cortesia"), client)
    row = q.insert.call_args[0][0]
    assert row["is_courtesy"] is True
    assert row["booking_fee_waived"] is True and row["booking_fee_paid_at"]
    assert out["kind"] == "cortesia"


@pytest.mark.asyncio
async def test_create_fee_waived():
    client, q = _sb([])
    out, _ = await _run(_req(billing="taxa_isenta"), client)
    row = q.insert.call_args[0][0]
    assert row["booking_fee_waived"] is True and row["is_courtesy"] is False
    assert out["kind"] == "taxa_isenta"


@pytest.mark.asyncio
async def test_create_split_both_parts():
    client, q = _sb([])
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    out, mocks = await _run(_req(split=True, first_consultation=True,
                                 parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}]), client)
    rows = [c[0][0] for c in q.insert.call_args_list]
    assert [r["session_note"] for r in rows] == ["1ª consulta · parte 1 de 2", "1ª consulta · parte 2 de 2"]
    assert all(r["consultation_type"] == "primeira_consulta" for r in rows)
    assert mocks[2].await_count == 2
    assert len(out["appointments"]) == 2


@pytest.mark.asyncio
async def test_complete_pending_part2_inherits_fee():
    client, q = _sb([])
    part1 = {"appointment_id": "evtP1", "booking_fee_paid_at": "2026-09-30T10:00:00-03:00",
             "booking_fee_waived": False, "is_courtesy": False}
    out, _ = await _run(_req(split_of=part1, first_consultation=True), client)
    row = q.insert.call_args[0][0]
    assert row["session_note"] == "1ª consulta · parte 2 de 2"
    assert row["booking_fee_paid_at"] == "2026-09-30T10:00:00-03:00"
    assert out["kind"] == "taxa_paga"


@pytest.mark.asyncio
async def test_rollback_deletes_all_events_on_db_failure():
    client, q = _sb([])
    q.execute = AsyncMock(side_effect=RuntimeError("db down"))
    tue = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    with pytest.raises(pb.BookingError):
        await _run(_req(split=True, first_consultation=True,
                        parts=[{"start": MON_9, "minutes": 60}, {"start": tue, "minutes": 60}]), client)


@pytest.mark.asyncio
async def test_rollback_calls_cancel_event():
    client, q = _sb([])
    q.execute = AsyncMock(side_effect=RuntimeError("db down"))
    from contextlib import ExitStack
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _patches(client)]
        with pytest.raises(pb.BookingError):
            await pb.create_appointments(_req())
    mocks[3].assert_awaited_once_with("cal", "evt1")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: FAIL nos testes novos.

- [ ] **Step 3: Implementar em `app/panel_booking.py`**

Acrescentar imports: `from app.database import log_event` e `from app.booking_texts import DOCTOR_LABELS, format_appt_line`.

```python
class BookingError(Exception):
    """Falha ao gravar; nada ficou criado (eventos do Calendar já apagados)."""


def _notify_clinic_async(subject: str, body: str, phone: str) -> None:
    from app.graph.tools import _notify_clinic
    asyncio.create_task(_notify_clinic(body, phone=phone, subject=subject))


def _fee_fields(req: dict, now_iso: str) -> tuple[dict, str]:
    """Campos de taxa da linha e o tipo de mensagem (booking_texts.confirmation_text)."""
    part1 = req.get("split_of")
    if part1:
        # 2ª parte herda a situação da 1ª, como confirm_appointment faz.
        fields = {
            "booking_fee_waived": bool(part1.get("booking_fee_waived")),
            "booking_fee_paid_at": part1.get("booking_fee_paid_at"),
            "is_courtesy": bool(part1.get("is_courtesy")),
        }
        if fields["is_courtesy"]:
            return fields, "cortesia"
        if fields["booking_fee_waived"]:
            return fields, "taxa_isenta"
        if fields["booking_fee_paid_at"]:
            return fields, "taxa_paga"
        return fields, "normal"
    billing = req["billing"]
    if billing == "cortesia":
        # booking_fee_waived também, para todo filtro antigo por taxa já pular a cortesia.
        return {"booking_fee_waived": True, "booking_fee_paid_at": now_iso, "is_courtesy": True}, "cortesia"
    if billing == "taxa_isenta":
        return {"booking_fee_waived": True, "booking_fee_paid_at": now_iso, "is_courtesy": False}, "taxa_isenta"
    return {"booking_fee_waived": False, "booking_fee_paid_at": None, "is_courtesy": False}, "normal"


def _part_notes(req: dict) -> list[str]:
    extra = (req.get("session_note") or "").strip()
    if req.get("split_of"):
        labels = [SPLIT_PART2]
    elif req.get("split"):
        labels = [SPLIT_PART1, SPLIT_PART2][: len(req["parts"])]
    else:
        labels = [""]
    return [" · ".join(x for x in (label, extra) if x) for label in labels]


async def create_appointments(req: dict) -> dict:
    """Cria o(s) evento(s) e a(s) linha(s). Tudo ou nada."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event, create_event

    doctor = req["doctor"]
    patient = req["patient"]
    name = patient.get("name") or ""
    display = f"{name} ({patient['social_name']})" if patient.get("social_name") else name
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        raise BookingError("calendário do médico não encontrado")

    now_iso = datetime.now(TZ).isoformat()
    fee, kind = _fee_fields(req, now_iso)
    ctype = "primeira_consulta" if req["first_consultation"] else None
    if not req["first_consultation"] and _is_minor_julio(patient, doctor):
        ctype = "acompanhamento"

    created: list[tuple[str, datetime, int, str]] = []
    try:
        for part, note in zip(req["parts"], _part_notes(req)):
            event_id = await create_event(
                calendar_id=calendar_id, start=part["start"], slot_minutes=part["minutes"],
                patient_name=display, doctor_name=DOCTOR_LABELS[doctor], session_note=note,
                modality=req["modality"], patient_email=patient.get("email") or "",
                patient_number=req["phone"],
            )
            created.append((event_id, part["start"], part["minutes"], note))
        client = await get_supabase()
        for event_id, start, minutes, note in created:
            await client.from_("appointments").insert({
                "patient_id": patient["id"],
                "contact_id": req["contact_id"],
                "doctor_id": DOCTOR_IDS[doctor],
                "appointment_id": event_id,
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=minutes)).isoformat(),
                "status": "scheduled",
                "modality": req["modality"],
                "consultation_type": ctype,
                "session_note": note or None,
                **fee,
            }).execute()
    except Exception as exc:
        _logger.exception("panel create_appointments falhou patient=%s", patient.get("id"))
        for event_id, *_ in created:
            try:
                await cancel_event(calendar_id, event_id)
            except Exception:
                _logger.exception("rollback: falha ao apagar evento %s", event_id)
        # Linhas já inseridas antes da falha (split): marcar como canceladas.
        try:
            client = await get_supabase()
            for event_id, *_ in created:
                await client.from_("appointments").update({"status": "cancelled"}).eq("appointment_id", event_id).execute()
        except Exception:
            _logger.exception("rollback: falha ao cancelar linhas")
        raise BookingError("não foi possível gravar a consulta") from exc

    lines = [format_appt_line(doctor, start, note) for _, start, _, note in created]
    for event_id, start, minutes, note in created:
        await log_event("appointment_booked", req["phone"], {
            "doctor": doctor, "datetime": start.replace(tzinfo=None).isoformat(),
            "duration_minutes": minutes, "patient_name": name, "session_note": note,
            "origem": "painel", "atendente": req.get("agent") or "", "encaixe": bool(req.get("encaixe")),
            "appointment_id": event_id,
        })
    _notify_clinic_async(
        f"Agendamento realizado — {display}",
        "Agendamento realizado pelo painel ✅\n"
        f"Paciente: {display}\n" + "\n".join(lines) +
        f"\nModalidade: {req['modality']}\nAtendente: {req.get('agent') or '—'}"
        + ("\n⚠️ Encaixe fora da grade" if req.get("encaixe") else ""),
        req["phone"],
    )
    return {
        "kind": kind,
        "lines": lines,
        "pending_part2": bool(req.get("split")) and len(created) == 1,
        "appointments": [
            {"appointment_id": e, "start": s.isoformat(), "minutes": m, "session_note": n,
             "contact_id": req["contact_id"]}
            for e, s, m, n in created
        ],
    }


def _is_minor_julio(patient: dict, doctor: str) -> bool:
    from app.patients import _compute_age
    age = _compute_age(patient.get("birth_date"))
    return doctor == "julio" and age is not None and age < 18
```

Conferir o valor exato do status de cancelamento usado no projeto (`grep -n '"cancel' app/graph/tools.py | head`) e usar o mesmo (`cancelled` ou `canceled`).

Observação sobre `consultation_type`: a Eva só preenche esse campo para menor com o Dr. Júlio. O painel segue a mesma regra: marcado vira `primeira_consulta`; desmarcado vira `acompanhamento` para menor com o Dr. Júlio e `None` para os demais.

- [ ] **Step 4: Rodar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "feat(painel): criar consulta pela Eva, com divisão infantil e cortesia por consulta"
```

---

### Task 8: `panel_booking` mensagem ao paciente

Destinatários pela regra da idade, janela de 24h, envio, `messages` e checkpoint.

**Files:**
- Modify: `app/panel_booking.py`
- Test: `tests/test_panel_booking.py`

- [ ] **Step 1: Testes que falham**

```python
@pytest.mark.asyncio
async def test_preview_recipients_follow_age_rule_and_report_held():
    contacts = [{"id": "c1", "phone": "5581999998888", "name": "Carla Menezes", "manual_hold": False}]
    linked = [{"contact": {"id": "c2", "name": "Paulo", "phone": "5581911112222", "manual_hold": True},
               "is_self": False, "relationship": "pai"}]
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=contacts) as m, \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=linked):
        out = await pb.message_preview("p1", "c1", "normal", ["Dr. Júlio — x"], pending_part2=False)
    m.assert_awaited_once_with("p1", {"contact_id": "c1"})
    assert out["recipients"] == [{"name": "Carla Menezes", "phone_hint": "8888"}]
    assert out["held"] == ["Paulo"]
    assert out["text"].startswith("Consulta registrada! ✅\nDr. Júlio — x")


@pytest.mark.asyncio
async def test_preview_pending_part2_adds_line():
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock,
               return_value=[{"id": "c1", "phone": "5581999998888", "name": "Carla"}]), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]):
        out = await pb.message_preview("p1", "c1", "taxa_isenta", ["Dr. Júlio — x"], pending_part2=True)
    assert "O horário da 2ª parte da primeira consulta será combinado depois." in out["text"]


@pytest.mark.asyncio
async def test_send_skips_closed_window_and_writes_checkpoint_when_open():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"},
              {"id": "c2", "phone": "5581911112222", "name": "Paulo"}]
    chatbot = MagicMock()
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, side_effect=[True, False]), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.panel_booking.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.graph.graph.chatbot", chatbot):
        out = await pb.send_booking_message("p1", "c1", "normal", ["Dr. Júlio — x"], pending_part2=False)
    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][0] == "5581999998888@s.whatsapp.net"
    mock_save.assert_awaited_once()
    chatbot.aupdate_state.assert_awaited_once()
    cfg = chatbot.aupdate_state.call_args[0][0]
    assert cfg["configurable"]["thread_id"] == "5581999998888@s.whatsapp.net"
    assert out["sent"] == ["Carla"] and out["not_delivered"] == ["Paulo"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_panel_booking.py -q -k "preview or send"`
Expected: FAIL.

- [ ] **Step 3: Implementar**

Imports novos no topo: `from app.patients import consultation_reminder_contacts, _linked_contacts_with_marker, _is_held`, `from app.whatsapp import send_text`, `from app.database import save_message`, `from app.booking_texts import confirmation_text`.

```python
_PENDING_PART2_LINE = "O horário da 2ª parte da primeira consulta será combinado depois."
WHATSAPP_WINDOW_HOURS = 24


def _thread(phone: str) -> str:
    p = (phone or "").lstrip("+")
    return p if p.endswith("@s.whatsapp.net") else f"{p}@s.whatsapp.net"


def _text_for(contact: dict, kind: str, lines: list[str], pending_part2: bool) -> str:
    name = display_name(contact.get("name") or "") or "tudo bem"
    txt = confirmation_text(kind, "\n".join(lines), name)
    if pending_part2:
        txt += f"\n\n{_PENDING_PART2_LINE}"
    return txt


async def _recipients(patient_id: str, contact_id: str) -> tuple[list[dict], list[str]]:
    recips = await consultation_reminder_contacts(patient_id, {"contact_id": contact_id})
    linked = await _linked_contacts_with_marker(patient_id, include_inactive=True)
    held = [lc["contact"].get("name") or "sem nome" for lc in linked if _is_held(lc["contact"])]
    return recips, held


async def message_preview(patient_id: str, contact_id: str, kind: str, lines: list[str],
                          pending_part2: bool) -> dict:
    recips, held = await _recipients(patient_id, contact_id)
    first = recips[0] if recips else {}
    return {
        "text": _text_for(first, kind, lines, pending_part2),
        "recipients": [{"name": c.get("name") or "", "phone_hint": (c.get("phone") or "")[-4:]} for c in recips],
        "held": held,
    }


async def _window_open(phone: str) -> bool:
    """Mesma regra de scripts/send_payment_reminders._window_open: fora de 24h da
    última mensagem do contato, o Meta descarta texto livre em silêncio.
    Erro na consulta = janela fechada (não finge que entregou)."""
    from app.phone import _phone_variants
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WHATSAPP_WINDOW_HOURS)).isoformat()
    try:
        client = await get_supabase()
        res = await (
            client.from_("messages").select("created_at")
            .in_("phone", _phone_variants(phone)).eq("role", "user")
            .gte("created_at", cutoff).limit(1).execute()
        )
        return bool(res.data)
    except Exception:
        _logger.exception("panel _window_open falhou phone=%s", phone)
        return False


async def send_booking_message(patient_id: str, contact_id: str, kind: str, lines: list[str],
                               pending_part2: bool) -> dict:
    from langchain_core.messages import AIMessage
    from app.graph import graph as graph_module

    recips, held = await _recipients(patient_id, contact_id)
    sent, not_delivered = [], []
    for c in recips:
        name = c.get("name") or "sem nome"
        phone = (c.get("phone") or "").lstrip("+")
        if not phone or not await _window_open(phone):
            not_delivered.append(name)
            continue
        text = _text_for(c, kind, lines, pending_part2)
        thread = _thread(phone)
        try:
            await send_text(thread, text)
        except Exception:
            _logger.exception("panel send_text falhou phone=%s", phone)
            not_delivered.append(name)
            continue
        await save_message(thread, "assistant", text)
        try:
            cfg = {"configurable": {"thread_id": thread, "phone": thread}}
            await graph_module.chatbot.aupdate_state(cfg, {"messages": [AIMessage(content=text)]}, as_node="patient_agent")
        except Exception:
            _logger.exception("panel checkpoint falhou phone=%s", phone)
        sent.append(name)
    return {"sent": sent, "not_delivered": not_delivered, "held": held}
```

Conferir: `from app.phone import _phone_variants` existe (usado em `scripts/send_payment_reminders.py`); `send_text` vive em `app/whatsapp.py`; `save_message` em `app/database.py`.

- [ ] **Step 4: Rodar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "feat(painel): mensagem da nova consulta pela regra da idade, com janela de 24h e memória da Eva"
```

---

### Task 9: Endpoint `POST /admin/panel/appointments`

Um endpoint só, com três modos. `dry_run=true` devolve a checagem e a prévia da mensagem sem gravar nada. Sem `encaixe_confirmed`, horário fora da grade ou ocupado devolve 409 com os motivos. Com `encaixe_confirmed=true`, pula a checagem.

**Corpo:**

```json
{
  "phone": "5581999998888", "patient_id": "p1", "doctor": "julio", "modality": "presencial",
  "parts": [{"start": "2026-10-05T09:00", "minutes": 60}],
  "split": false, "split_of": null, "session_note": "", "first_consultation": false,
  "billing": "normal", "encaixe_confirmed": false, "dry_run": false, "agent": "Maria"
}
```

**Respostas:**
- 200 `dry_run`: `{"encaixe_reasons": [...], "message": {"text", "recipients", "held"}}`
- 409: `{"detail": {"needs_encaixe": true, "reasons": [...]}}`
- 200 criado: `{"appointments": [...], "message": {"sent", "not_delivered", "held"}}`
- 400 validação, 403 segredo, 502 falha de gravação (`BookingError`).

**Files:**
- Modify: `app/main.py` (depois de `/admin/patch-state`)
- Modify: `app/panel_booking.py` (validação)
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Testes que falham**

Em `tests/test_webhook.py`:

```python
# ── /admin/panel/appointments ─────────────────────────────────────────────────
_PANEL_BODY = {
    "phone": "5581999998888", "patient_id": "p1", "doctor": "julio", "modality": "presencial",
    "parts": [{"start": "2026-10-05T09:00", "minutes": 60}], "split": False, "split_of": None,
    "session_note": "", "first_consultation": False, "billing": "normal",
    "encaixe_confirmed": False, "dry_run": False, "agent": "Maria",
}
_PATIENT = {"id": "p1", "name": "Ana Souza", "birth_date": "01/01/1990", "modality_restriction": None,
            "custom_price": None, "booking_fee_waived": False, "social_name": None, "email": None}


def _panel_patches(check=(), created=None):
    from unittest.mock import AsyncMock, patch
    return [
        patch("app.panel_booking.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "c1"}),
        patch("app.panel_booking.get_patient_by_id", new_callable=AsyncMock, return_value=_PATIENT),
        patch("app.panel_booking.check_slot", new_callable=AsyncMock, return_value=list(check)),
        patch("app.panel_booking.create_appointments", new_callable=AsyncMock,
              return_value=created or {"kind": "normal", "lines": ["l"], "pending_part2": False, "appointments": [{"appointment_id": "e1"}]}),
        patch("app.panel_booking.send_booking_message", new_callable=AsyncMock,
              return_value={"sent": ["Ana"], "not_delivered": [], "held": []}),
        patch("app.panel_booking.message_preview", new_callable=AsyncMock,
              return_value={"text": "t", "recipients": [], "held": []}),
    ]


def _post_panel(http_client, monkeypatch, body, secret="s3cr3t"):
    from contextlib import ExitStack
    monkeypatch.setenv("ADMIN_SECRET", "s3cr3t")
    with ExitStack() as st:
        mocks = [st.enter_context(p) for p in _panel_patches(*_post_panel.args)]
        r = http_client.post("/admin/panel/appointments", json=body, headers={"X-Admin-Secret": secret})
    return r, mocks
_post_panel.args = ()


def test_panel_requires_secret(http_client, monkeypatch):
    r, _ = _post_panel(http_client, monkeypatch, _PANEL_BODY, secret="errado")
    assert r.status_code == 403


def test_panel_creates_when_slot_free(http_client, monkeypatch):
    r, mocks = _post_panel(http_client, monkeypatch, _PANEL_BODY)
    assert r.status_code == 200
    assert r.json()["message"]["sent"] == ["Ana"]
    mocks[3].assert_awaited_once()


def test_panel_encaixe_needs_confirmation(http_client, monkeypatch):
    _post_panel.args = (["fora do horário de atendimento"],)
    try:
        r, mocks = _post_panel(http_client, monkeypatch, _PANEL_BODY)
    finally:
        _post_panel.args = ()
    assert r.status_code == 409
    assert r.json()["detail"] == {"needs_encaixe": True, "reasons": ["fora do horário de atendimento"]}
    mocks[3].assert_not_awaited()


def test_panel_encaixe_confirmed_skips_check(http_client, monkeypatch):
    _post_panel.args = (["fora do horário de atendimento"],)
    try:
        r, mocks = _post_panel(http_client, monkeypatch, {**_PANEL_BODY, "encaixe_confirmed": True})
    finally:
        _post_panel.args = ()
    assert r.status_code == 200
    mocks[2].assert_not_awaited()
    assert mocks[3].call_args[0][0]["encaixe"] is True


def test_panel_dry_run_writes_nothing(http_client, monkeypatch):
    r, mocks = _post_panel(http_client, monkeypatch, {**_PANEL_BODY, "dry_run": True})
    assert r.status_code == 200
    assert set(r.json()) == {"encaixe_reasons", "message"}
    mocks[3].assert_not_awaited()
    mocks[4].assert_not_awaited()


def test_panel_rejects_40min_for_julio(http_client, monkeypatch):
    body = {**_PANEL_BODY, "parts": [{"start": "2026-10-05T09:00", "minutes": 40}]}
    r, _ = _post_panel(http_client, monkeypatch, body)
    assert r.status_code == 400


def test_panel_rejects_split_for_adult(http_client, monkeypatch):
    body = {**_PANEL_BODY, "split": True, "first_consultation": True}
    r, _ = _post_panel(http_client, monkeypatch, body)
    assert r.status_code == 400


def test_panel_rejects_modality_against_restriction(http_client, monkeypatch):
    global _PATIENT
    old = _PATIENT
    _PATIENT = {**old, "modality_restriction": "online"}
    try:
        r, _ = _post_panel(http_client, monkeypatch, _PANEL_BODY)
    finally:
        _PATIENT = old
    assert r.status_code == 400
```

(Se o truque com `_post_panel.args` ficar confuso, o implementador pode trocar por um parâmetro `check=` explícito na função auxiliar; o que importa são as asserções.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_webhook.py -q -k panel`
Expected: FAIL, 404 no endpoint.

- [ ] **Step 3: Validação em `app/panel_booking.py`**

Imports: `from app.patients import get_contact_by_phone, get_patient_by_id, _compute_age`.

```python
class PanelInputError(ValueError):
    pass


def _parse_start(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=TZ)
    except (TypeError, ValueError):
        raise PanelInputError(f"data/hora inválida: {raw}")


async def build_request(body: dict) -> dict:
    """Valida o corpo do painel e devolve o dict que create_appointments espera."""
    doctor = body.get("doctor")
    if doctor not in DOCTOR_IDS:
        raise PanelInputError("médico inválido")
    modality = body.get("modality")
    if modality not in ("online", "presencial"):
        raise PanelInputError("modalidade inválida")
    billing = body.get("billing", "normal")
    if billing not in ("normal", "taxa_isenta", "cortesia"):
        raise PanelInputError("cobrança inválida")

    phone = _thread(body.get("phone") or "")
    contact = await get_contact_by_phone(phone)
    if not contact:
        raise PanelInputError("contato não encontrado")
    patient = await get_patient_by_id(body.get("patient_id") or "")
    if not patient:
        raise PanelInputError("paciente não encontrado")

    restriction = patient.get("modality_restriction")
    if restriction in ("online", "presencial") and modality != restriction:
        raise PanelInputError(f"este paciente só pode ser atendido {restriction}")

    raw_parts = body.get("parts") or []
    if not 1 <= len(raw_parts) <= 2:
        raise PanelInputError("informe um ou dois horários")
    parts = []
    for p in raw_parts:
        minutes = int(p.get("minutes") or 0)
        if minutes not in (40, 60, 120) or (minutes == 40 and doctor != "bruna"):
            raise PanelInputError("duração inválida")
        parts.append({"start": _parse_start(p.get("start")), "minutes": minutes})

    split = bool(body.get("split"))
    split_of_id = body.get("split_of")
    split_of = None
    if split or split_of_id:
        age = _compute_age(patient.get("birth_date"))
        if not (doctor == "julio" and age is not None and age < 18 and body.get("first_consultation")):
            raise PanelInputError("divisão só vale para a 1ª consulta de menor com o Dr. Júlio")
        if any(p["minutes"] != 60 for p in parts):
            raise PanelInputError("cada parte da 1ª consulta dividida tem 1h")
    if len(parts) == 2 and not split:
        raise PanelInputError("dois horários só na 1ª consulta dividida")
    if split_of_id:
        if len(parts) != 1:
            raise PanelInputError("completar a 2ª parte usa um horário só")
        client = await get_supabase()
        res = await (
            client.from_("appointments")
            .select("appointment_id, patient_id, booking_fee_paid_at, booking_fee_waived, is_courtesy, session_note, status")
            .eq("appointment_id", split_of_id).limit(1).execute()
        )
        split_of = (res.data or [None])[0]
        if not split_of or split_of["patient_id"] != patient["id"] or split_of.get("status") != "scheduled" \
                or not (split_of.get("session_note") or "").startswith(SPLIT_PART1):
            raise PanelInputError("parte 1 pendente não encontrada")

    return {
        "phone": phone, "contact_id": contact["id"], "patient": patient, "doctor": doctor,
        "modality": modality, "parts": parts, "split": split and not split_of_id, "split_of": split_of,
        "session_note": (body.get("session_note") or "").strip()[:80],
        "first_consultation": bool(body.get("first_consultation")), "billing": billing,
        "encaixe": False, "agent": (body.get("agent") or "").strip()[:80],
    }


async def handle(body: dict) -> tuple[int, dict]:
    """Fluxo do endpoint. Devolve (status_http, corpo)."""
    req = await build_request(body)
    reasons: list[str] = []
    if not body.get("encaixe_confirmed"):
        for part in req["parts"]:
            reasons += await check_slot(req["doctor"], part["start"], part["minutes"], req["patient"]["id"])
    if body.get("dry_run"):
        _, kind = _fee_fields(req, datetime.now(TZ).isoformat())
        lines = [format_appt_line(req["doctor"], p["start"], n) for p, n in zip(req["parts"], _part_notes(req))]
        pending = req["split"] and len(req["parts"]) == 1
        preview = await message_preview(req["patient"]["id"], req["contact_id"], kind, lines, pending)
        return 200, {"encaixe_reasons": reasons, "message": preview}
    if reasons:
        return 409, {"detail": {"needs_encaixe": True, "reasons": reasons}}
    req["encaixe"] = bool(body.get("encaixe_confirmed"))
    created = await create_appointments(req)
    msg = await send_booking_message(req["patient"]["id"], req["contact_id"], created["kind"],
                                     created["lines"], created["pending_part2"])
    return 200, {"appointments": created["appointments"], "message": msg}
```

Nota: `encaixe` fica verdadeiro só quando a atendente confirmou. Se `encaixe_confirmed` vier sem necessidade, ainda assim é registrado como encaixe; aceitável, porque o painel só manda `true` depois de um 409.

- [ ] **Step 4: Endpoint em `app/main.py`**

```python
@app.post("/admin/panel/appointments")
async def admin_panel_appointments(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Nova consulta criada pela atendente no painel. Ver app/panel_booking.py."""
    _check_admin_secret(x_admin_secret)
    from fastapi.responses import JSONResponse
    from app import panel_booking
    body = await request.json()
    try:
        status, payload = await panel_booking.handle(body)
    except panel_booking.PanelInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except panel_booking.BookingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return JSONResponse(status_code=status, content=payload)
```

Os testes do Step 1 fazem patch em `app.panel_booking.check_slot` e afins; como `handle` chama essas funções pelo nome do módulo, o patch funciona.

- [ ] **Step 5: Rodar**

Run: `uv run pytest tests/test_webhook.py tests/test_panel_booking.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/panel_booking.py tests/test_webhook.py
git commit -m "feat(eva): endpoint /admin/panel/appointments com prévia, encaixe e criação"
```

---

### Task 10: Painel, cliente da Eva

**Files:**
- Modify: `dashboard/pyproject.toml` (mover `httpx>=0.27.0` para `dependencies`)
- Create: `dashboard/eva_client.py`
- Test: `dashboard/tests/test_eva_client.py`

- [ ] **Step 1: Testes que falham**

```python
# dashboard/tests/test_eva_client.py
import httpx
import pytest

import eva_client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EVA_BASE_URL", "https://eva.test/")
    monkeypatch.setenv("ADMIN_SECRET", "s3cr3t")


def _mock(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(eva_client.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


@pytest.mark.asyncio
async def test_sends_secret_and_returns_json(monkeypatch):
    seen = {}
    def handler(req):
        seen["url"] = str(req.url)
        seen["secret"] = req.headers.get("X-Admin-Secret")
        return httpx.Response(200, json={"ok": 1})
    _mock(monkeypatch, handler)
    status, body = await eva_client.post("/admin/panel/appointments", {"a": 1})
    assert (status, body) == (200, {"ok": 1})
    assert seen == {"url": "https://eva.test/admin/panel/appointments", "secret": "s3cr3t"}


@pytest.mark.asyncio
async def test_passes_409_through(monkeypatch):
    _mock(monkeypatch, lambda req: httpx.Response(409, json={"detail": {"needs_encaixe": True, "reasons": ["x"]}}))
    status, body = await eva_client.post("/x", {})
    assert status == 409 and body["detail"]["needs_encaixe"] is True


@pytest.mark.asyncio
async def test_network_error_raises_eva_unavailable(monkeypatch):
    def handler(req):
        raise httpx.ConnectError("down")
    _mock(monkeypatch, handler)
    with pytest.raises(eva_client.EvaUnavailable):
        await eva_client.post("/x", {})


@pytest.mark.asyncio
async def test_missing_config_raises(monkeypatch):
    monkeypatch.delenv("EVA_BASE_URL")
    with pytest.raises(eva_client.EvaUnavailable):
        await eva_client.post("/x", {})
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_eva_client.py -q`
Expected: FAIL, módulo inexistente.

- [ ] **Step 3: Implementar**

```python
# dashboard/eva_client.py
"""Chamada do painel aos endpoints internos da Eva (/admin/*).

Env: EVA_BASE_URL (ex.: https://psiqueai.ayexa.com.br) e ADMIN_SECRET, o mesmo
valor configurado na Eva."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)


class EvaUnavailable(Exception):
    """Eva fora do ar, sem configuração, ou resposta ilegível."""


async def post(path: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    base = os.getenv("EVA_BASE_URL", "").rstrip("/")
    secret = os.getenv("ADMIN_SECRET", "")
    if not base or not secret:
        raise EvaUnavailable("painel sem EVA_BASE_URL/ADMIN_SECRET")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}{path}", json=body, headers={"X-Admin-Secret": secret})
        return r.status_code, r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("eva_client: falha em %s", path)
        raise EvaUnavailable(str(exc)) from exc
```

- [ ] **Step 4: Rodar**

Run: `cd dashboard && uv sync && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/pyproject.toml dashboard/uv.lock dashboard/eva_client.py dashboard/tests/test_eva_client.py
git commit -m "feat(painel): cliente dos endpoints internos da Eva"
```

---

### Task 11: Painel, leitura das consultas e etiqueta "1ª consulta"

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Testes que falham**

```python
# dashboard/tests/test_attendant_db.py (acrescentar)
from datetime import date

JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"


@pytest.mark.asyncio
async def test_list_consultas_future_active_only(patched_client, fake_client):
    fake_client.store["appointments"] = [
        {"appointment_id": "a1", "patient_id": "p1", "status": "scheduled", "doctor_id": JULIO,
         "start_time": "2099-10-05T12:00:00+00:00", "end_time": "2099-10-05T13:00:00+00:00",
         "modality": "presencial", "consultation_type": "primeira_consulta",
         "session_note": "1ª consulta · parte 1 de 2", "is_courtesy": False},
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
    assert out["has_completed"] is True


@pytest.mark.asyncio
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
    assert out["has_completed"] is False


@pytest.mark.asyncio
async def test_set_first_consultation(patched_client, fake_client):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "consultation_type": None}]
    await attendant_db.set_first_consultation("a1", True)
    assert fake_client.store["appointments"][0]["consultation_type"] == "primeira_consulta"
    await attendant_db.set_first_consultation("a1", False)
    assert fake_client.store["appointments"][0]["consultation_type"] == "acompanhamento"


def test_age_on():
    assert attendant_db.age_on("10/02/2016", date(2026, 2, 9)) == 9
    assert attendant_db.age_on("2016-02-10", date(2026, 2, 10)) == 10
    assert attendant_db.age_on("", date(2026, 1, 1)) is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k "consultas or first_consultation or age_on"`
Expected: FAIL.

- [ ] **Step 3: Implementar em `dashboard/attendant_db.py`**

```python
# Mesmos textos de app/panel_booking.py (SPLIT_PART1/SPLIT_PART2).
SPLIT_PART1 = "1ª consulta · parte 1 de 2"
SPLIT_PART2 = "1ª consulta · parte 2 de 2"
# Mesmo mapa de dashboard/payments.py DOCTOR_KEY.
_DOCTOR_KEY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def age_on(birth_date: str | None, day: date) -> int | None:
    """Idade em `day`. Aceita dd/mm/aaaa e ISO, como patients.birth_date."""
    raw = (birth_date or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            bd = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None
    return day.year - bd.year - ((day.month, day.day) < (bd.month, bd.day))


def _local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ)


async def list_consultas(patient_id: str) -> dict:
    """Consultas que ainda não aconteceram (scheduled/pending_reschedule), sem
    dados de pagamento, mais: a parte 1 de 1ª consulta dividida cuja parte 2
    ainda não foi marcada, e se o paciente já teve consulta realizada."""
    client = await get_client()
    now_iso = datetime.now(_TZ).isoformat()
    res = await (
        client.from_("appointments")
        .select("appointment_id, start_time, end_time, doctor_id, modality, consultation_type, "
                "session_note, status, is_courtesy")
        .eq("patient_id", patient_id)
        .in_("status", list(_ACTIVE_APPT_STATUSES))
        .gt("start_time", now_iso)
        .order("start_time")
        .execute()
    )
    appts = []
    for r in res.data or []:
        start, end = _local(r["start_time"]), _local(r["end_time"])
        appts.append({
            "appointment_id": r["appointment_id"],
            "start_local": start.strftime("%Y-%m-%dT%H:%M"),
            "minutes": int((end - start).total_seconds() // 60),
            "doctor_key": _DOCTOR_KEY.get(r.get("doctor_id"), ""),
            "modality": r.get("modality") or "",
            "consultation_type": r.get("consultation_type"),
            "session_note": r.get("session_note") or "",
            "status": r["status"],
        })
    notes = [a["session_note"] for a in appts]
    pending = None
    if not any(n.startswith(SPLIT_PART2) for n in notes):
        pending = next((a["appointment_id"] for a in appts
                        if a["session_note"].startswith(SPLIT_PART1) and a["status"] == "scheduled"), None)
    done = await (
        client.from_("appointments").select("id").eq("patient_id", patient_id)
        .eq("status", "completed").limit(1).execute()
    )
    return {"appointments": appts, "pending_part2": pending, "has_completed": bool(done.data)}


async def set_first_consultation(appointment_id: str, first: bool) -> None:
    """Liga/desliga a etiqueta "1ª consulta". Só banco; não avisa o paciente."""
    client = await get_client()
    await (
        client.from_("appointments")
        .update({"consultation_type": "primeira_consulta" if first else "acompanhamento"})
        .eq("appointment_id", appointment_id)
        .execute()
    )
```

Garantir que `date`, `datetime`, `timezone` estão importados no topo do módulo.

- [ ] **Step 4: Rodar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): leitura das consultas futuras e etiqueta 1ª consulta"
```

---

### Task 12: Painel, rotas de consultas

**Files:**
- Modify: `dashboard/attendant_routes.py`
- Test: `dashboard/tests/test_attendant_routes.py`, `dashboard/tests/test_attendant_scope.py`

Rotas:
- `GET /api/atendente/consultas?phone=&patient_id=` → `list_consultas` (escopo do paciente).
- `POST /api/atendente/consulta/{appointment_id}/primeira` `{phone, first, agent}` → escopo da consulta, `set_first_consultation`, `log_event("attendant_first_consultation")`.
- `POST /api/atendente/consulta/nova` `{phone, patient_id, ...campos do corpo da Eva}` → escopo do paciente, repassa à Eva com `phone` do corpo, `agent` limitado a 80, e devolve o status e corpo da Eva. `EvaUnavailable` vira 503 `"A Eva não respondeu. Nada foi alterado."`. Loga `attendant_new_appointment` quando status 200 e não é `dry_run`.

- [ ] **Step 1: Testes que falham**

Em `dashboard/tests/test_attendant_routes.py`:

```python
import eva_client


def test_consultas_returns_list(client, monkeypatch):
    async def fake(pid):
        return {"appointments": [], "pending_part2": None, "has_completed": False, "pid": pid}
    monkeypatch.setattr(attendant_db, "list_consultas", fake)
    r = client.get("/api/atendente/consultas", params={"token": "test-token", "phone": "5581", "patient_id": "p1"})
    assert r.status_code == 200 and r.json()["pid"] == "p1"


def test_first_consultation_toggle(client, monkeypatch):
    calls = {}
    async def fake_set(aid, first):
        calls["set"] = (aid, first)
    async def fake_log(t, phone, meta):
        calls["log"] = t
    monkeypatch.setattr(attendant_db, "set_first_consultation", fake_set)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/consulta/a1/primeira", params={"token": "test-token"},
                    json={"phone": "5581", "first": False, "agent": "Maria"})
    assert r.status_code == 200
    assert calls == {"set": ("a1", False), "log": "attendant_first_consultation"}


def test_nova_consulta_forwards_to_eva(client, monkeypatch):
    seen = {}
    async def fake_post(path, body):
        seen["path"], seen["body"] = path, body
        return 200, {"appointments": [{"appointment_id": "e1"}], "message": {"sent": ["Ana"]}}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    body = {"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "presencial",
            "parts": [{"start": "2026-10-05T09:00", "minutes": 60}], "billing": "normal", "agent": "Maria"}
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"}, json=body)
    assert r.status_code == 200
    assert seen["path"] == "/admin/panel/appointments"
    assert seen["body"]["patient_id"] == "p1" and seen["body"]["phone"] == "5581"
    assert seen["log"] == "attendant_new_appointment"


def test_nova_consulta_passes_409(client, monkeypatch):
    async def fake_post(path, body):
        return 409, {"detail": {"needs_encaixe": True, "reasons": ["dia bloqueado na agenda"]}}
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 409
    assert r.json()["detail"]["reasons"] == ["dia bloqueado na agenda"]


def test_nova_consulta_eva_down(client, monkeypatch):
    async def fake_post(path, body):
        raise eva_client.EvaUnavailable("down")
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p1", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 503
    assert "Nada foi alterado" in r.json()["detail"]
```

Em `dashboard/tests/test_attendant_scope.py`, seguindo o padrão `_scope(monkeypatch, contact_id, patient_ids)`:

```python
def test_consultas_out_of_scope(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    r = client.get("/api/atendente/consultas", params={"token": "test-token", "phone": "5581", "patient_id": "p9"})
    assert r.status_code == 403


def test_nova_consulta_out_of_scope(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    r = client.post("/api/atendente/consulta/nova", params={"token": "test-token"},
                    json={"phone": "5581", "patient_id": "p9", "doctor": "julio", "modality": "online",
                          "parts": [{"start": "2026-10-05T09:00", "minutes": 60}]})
    assert r.status_code == 403


def test_first_consultation_out_of_scope(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    async def other(aid):
        return "p9"
    monkeypatch.setattr(attendant_db, "get_appointment_patient_id", other)
    r = client.post("/api/atendente/consulta/a1/primeira", params={"token": "test-token"},
                    json={"phone": "5581", "first": True})
    assert r.status_code == 403
```

Ler o topo de `test_attendant_scope.py` para usar o fixture de cliente certo.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py tests/test_attendant_scope.py -q`
Expected: FAIL (404).

- [ ] **Step 3: Implementar em `dashboard/attendant_routes.py`**

```python
import eva_client
from fastapi.responses import JSONResponse


class FirstBody(BaseModel):
    phone: str
    first: bool
    agent: str = Field(default="", max_length=80)


class NewAppointmentBody(BaseModel):
    phone: str
    patient_id: str
    doctor: str
    modality: str
    parts: list[dict]
    split: bool = False
    split_of: str | None = None
    session_note: str = Field(default="", max_length=80)
    first_consultation: bool = False
    billing: str = "normal"
    encaixe_confirmed: bool = False
    dry_run: bool = False
    agent: str = Field(default="", max_length=80)


@router.get("/consultas")
async def get_consultas(phone: str, patient_id: str, _: None = Depends(verify_token)):
    await _assert_patient_scope(phone, patient_id)
    return await attendant_db.list_consultas(patient_id)


@router.post("/consulta/{appointment_id}/primeira")
async def set_primeira(appointment_id: str, body: FirstBody, _: None = Depends(verify_token)):
    await _assert_appointment_scope(body.phone, appointment_id)
    await attendant_db.set_first_consultation(appointment_id, body.first)
    await attendant_db.log_event("attendant_first_consultation", body.phone,
                                 {"appointment_id": appointment_id, "first": body.first, "agent": body.agent})
    return {"ok": True}


@router.post("/consulta/nova")
async def nova_consulta(body: NewAppointmentBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, body.patient_id)
    try:
        status, payload = await eva_client.post("/admin/panel/appointments", body.model_dump())
    except eva_client.EvaUnavailable:
        raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
    if status == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_new_appointment", body.phone, {
            "patient_id": body.patient_id, "agent": body.agent, "encaixe": body.encaixe_confirmed,
            "appointments": [a.get("appointment_id") for a in payload.get("appointments", [])],
        })
    return JSONResponse(status_code=status, content=payload)
```

O teste de escopo da `/primeira` depende de `_assert_appointment_scope` usar `attendant_db.get_appointment_patient_id` (já é assim). O fixture `_bypass_scope` de `test_attendant_routes.py` já anula os `_assert_*`.

- [ ] **Step 4: Rodar**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/
git commit -m "feat(painel): rotas de consultas (lista, etiqueta 1ª consulta, nova consulta via Eva)"
```

---

### Task 13: Painel, aba Consultas

A aba abre primeiro. Topo do paciente (nome em serifa, etiqueta Infantil, idade, médico, quantidade de consultas, botão "Nova consulta"), linha do tempo vertical e a pendência de 2ª parte tracejada com "Marcar". Sem pagamento aqui. Os ícones de alterar e cancelar entram na Parte 3; nesta parte o cartão só tem o interruptor da etiqueta "1ª consulta" quando o paciente é menor.

**Files:**
- Modify: `dashboard/templates/atendente.html`
- Test: `dashboard/tests/test_main_auth.py` (a página renderiza com a aba nova)

- [ ] **Step 1: Teste da página**

Acrescentar em `dashboard/tests/test_main_auth.py`, seguindo o teste que já renderiza `/atendente`:

```python
def test_atendente_page_has_consultas_tab_first(client):
    r = client.get("/atendente", params={"token": "test-token"})
    assert r.status_code == 200
    html = r.text
    assert 'data-tab="consultas"' in html
    assert html.index('data-tab="consultas"') < html.index('data-tab="financeiro"')
    assert 'id="tab-consultas"' in html
    assert 'id="appt-sheet"' in html
```

(ajustar o nome do fixture ao que o arquivo usa). Rodar e ver falhar.

- [ ] **Step 2: CSS**

Dentro de `.eva { ... }` acrescentar os tokens, e no bloco escuro os equivalentes:

```css
    --info: #2F6FB3; --info-soft: #E6EFF9; --warn: #A2670B; --warn-soft: #FBF1DF;
```

```css
           --info: #8DB8EA; --info-soft: #1D2A3A; --warn: #E7B45A; --warn-soft: #33291A;
```

Classes novas, antes de `</style>`:

```css
  .eva .tag { display: inline-flex; align-items: center; height: 22px; padding: 0 8px; border-radius: 99px;
              font-size: 11px; font-weight: 500; background: var(--soft); color: var(--muted); }
  .eva .tag-kid { background: var(--accent-soft); color: var(--accent-dk); }
  .eva .tag-info { background: var(--info-soft); color: var(--info); }
  .eva .timeline { position: relative; display: flex; flex-direction: column; gap: 14px; padding-left: 0; margin: 0; list-style: none; }
  .eva .tl-row { display: grid; grid-template-columns: 64px 18px 1fr; gap: 12px; align-items: start; }
  .eva .tl-date { text-align: right; line-height: 1; padding-top: 12px; }
  .eva .tl-day { font-size: 28px; }
  .eva .tl-mon { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  .eva .tl-rail { position: relative; align-self: stretch; }
  .eva .tl-rail::before { content: ""; position: absolute; left: 8px; top: 0; bottom: -14px; width: 1px; background: var(--line); }
  .eva .tl-row:last-child .tl-rail::before { bottom: 50%; }
  .eva .tl-dot { position: absolute; left: 3px; top: 18px; width: 11px; height: 11px; border-radius: 99px;
                 background: var(--accent); box-shadow: 0 0 0 3px var(--bg); }
  .eva .tl-dot.dashed { background: var(--bg); border: 1.5px dashed var(--accent); box-sizing: border-box; }
  .eva .tl-card { padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; }
  .eva .tl-card.pending { border-style: dashed; background: transparent; }
  .eva .seg { display: inline-flex; padding: 3px; border-radius: 10px; background: var(--soft); gap: 2px; flex-wrap: wrap; }
  .eva .seg button { height: 32px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent;
                     font: inherit; font-size: 13px; color: var(--muted); cursor: pointer; }
  .eva .seg button[aria-checked="true"] { background: var(--surface); color: var(--ink); font-weight: 600;
                                          box-shadow: 0 1px 2px rgba(66,66,74,.12); }
  .eva .seg button:disabled { opacity: .4; cursor: default; }
  .eva .btn-info { background: var(--info); color: #FFFFFF; border-color: var(--info); }
  .eva .notice { border-radius: 12px; padding: 12px 14px; font-size: 13px; line-height: 1.45; }
  .eva .notice-info { background: var(--info-soft); color: var(--info); }
  .eva .notice-warn { background: var(--warn-soft); color: var(--warn); }
```

- [ ] **Step 3: Marcação da aba**

Na `<nav id="tab-buttons">`, inserir como primeiro botão e tirar o `aria-selected="true"` do Financeiro:

```html
      <button type="button" role="tab" data-tab="consultas" class="tab" aria-selected="true">Consultas</button>
      <button type="button" role="tab" data-tab="financeiro" class="tab" aria-selected="false">Financeiro</button>
```

Acrescentar `hidden` à `section#tab-financeiro` e, antes dela:

```html
    <section id="tab-consultas" class="tab-panel flex flex-col gap-5">
      <div id="consultas-head" class="flex items-end gap-4 flex-wrap"></div>
      <ol id="consultas-list" class="timeline" aria-label="Consultas agendadas"></ol>
      <p id="consultas-empty" class="hidden text-sm" style="color: var(--muted)">Nenhuma consulta marcada.</p>
    </section>
```

- [ ] **Step 4: JS da aba**

Globais novos, junto aos existentes:

```js
let CONSULTAS_SEQ = 0;
let CURRENT_PATIENT = null;   // linha de patients do paciente selecionado
let CONSULTAS = { appointments: [], pending_part2: null, has_completed: false };
const SPLIT_PART1 = "1ª consulta · parte 1 de 2";   // mesmo texto em app/panel_booking.py
const SPLIT_PART2 = "1ª consulta · parte 2 de 2";
const DOCTOR_KEYS = { "d5baa58b-a788-4f40-b8c0-512c189150be": "julio", "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna" };
const DOCTOR_LABEL = { julio: "Dr. Júlio", bruna: "Dra. Bruna" };
const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const WEEKDAYS = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
```

Funções:

```js
function ageOn(birth, isoDay) {
  if (!birth) return null;
  let d, m, y;
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(birth)) [d, m, y] = birth.split("/").map(Number);
  else if (/^\d{4}-\d{2}-\d{2}/.test(birth)) [y, m, d] = birth.slice(0, 10).split("-").map(Number);
  else return null;
  const [ty, tm, td] = isoDay.slice(0, 10).split("-").map(Number);
  return ty - y - ((tm < m || (tm === m && td < d)) ? 1 : 0);
}
function todayIso() { return new Date().toLocaleDateString("sv-SE", { timeZone: "America/Recife" }); }
function isMinor(p, isoDay) { const a = ageOn(p && p.birth_date, isoDay || todayIso()); return a !== null && a < 18; }
function fmtDur(min) { return min === 120 ? "2h" : min === 60 ? "1h" : `${min}min`; }

async function loadConsultas(pid) {
  const seq = ++CONSULTAS_SEQ;
  const r = await fetch(api("/api/atendente/consultas", { phone: PHONE, patient_id: pid }));
  if (seq !== CONSULTAS_SEQ || pid !== CURRENT_PID) return;
  if (!r.ok) { $("consultas-list").innerHTML = ""; setStatus("Erro ao carregar consultas."); return; }
  CONSULTAS = await r.json();
  if (seq !== CONSULTAS_SEQ || pid !== CURRENT_PID) return;
  renderConsultas();
}

function renderConsultasHead() {
  const p = CURRENT_PATIENT;
  if (!p) { $("consultas-head").innerHTML = ""; return; }
  const age = ageOn(p.birth_date, todayIso());
  const doc = DOCTOR_LABEL[DOCTOR_KEYS[p.doctor_id]] || "";
  const n = CONSULTAS.appointments.length;
  $("consultas-head").innerHTML = `
    <div class="flex-1 flex flex-col gap-1">
      <div class="flex items-center gap-2 flex-wrap">
        <h2 class="serif" style="font-size: 28px; margin: 0">${escapeHtml(p.name || "")}</h2>
        ${age !== null && age < 18 ? '<span class="tag tag-kid">Infantil</span>' : ""}
      </div>
      <span class="text-sm" style="color: var(--muted)">${[age !== null ? `${age} anos` : "", doc, n ? `${n} marcada${n > 1 ? "s" : ""}` : ""].filter(Boolean).map(escapeHtml).join(" · ")}</span>
    </div>
    <button id="appt-new" type="button" class="btn btn-primary">Nova consulta</button>`;
  $("appt-new").onclick = () => openApptSheet(null);
}

function apptCard(a) {
  const [y, m, d] = a.start_local.slice(0, 10).split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  const minor = isMinor(CURRENT_PATIENT, a.start_local);
  const first = a.consultation_type === "primeira_consulta";
  const tags = [];
  if (a.session_note.startsWith(SPLIT_PART1)) tags.push("parte 1 de 2");
  else if (a.session_note.startsWith(SPLIT_PART2)) tags.push("parte 2 de 2");
  const extra = a.session_note.replace(SPLIT_PART1, "").replace(SPLIT_PART2, "").replace(/^ · /, "");
  if (extra) tags.push(extra);
  if (a.status === "pending_reschedule") tags.push("aguardando remarcação");
  return `<li class="tl-row">
    <div class="tl-date"><div class="serif tl-day num">${d}</div><div class="tl-mon">${MONTHS[m - 1]} · ${WEEKDAYS[dt.getDay()]}</div></div>
    <div class="tl-rail"><span class="tl-dot"></span></div>
    <div class="card tl-card">
      <div class="flex items-baseline gap-2 flex-wrap">
        <span class="serif num" style="font-size: 20px">${a.start_local.slice(11, 16)}</span>
        <span class="text-sm" style="color: var(--muted)">${escapeHtml([DOCTOR_LABEL[a.doctor_key] || "", a.modality === "online" ? "Online" : a.modality === "presencial" ? "Presencial" : "", fmtDur(a.minutes)].filter(Boolean).join(" · "))}</span>
      </div>
      <div class="flex items-center gap-2 flex-wrap">
        ${minor ? `<button type="button" class="switch" role="switch" aria-checked="${first}" aria-label="1ª consulta" data-first="${escapeHtml(a.appointment_id)}"></button><span class="text-xs" style="color: var(--muted)">1ª consulta</span>` : ""}
        ${tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("")}
      </div>
    </div></li>`;
}

function renderConsultas() {
  renderConsultasHead();
  const list = CONSULTAS.appointments;
  let html = list.map(apptCard).join("");
  if (CONSULTAS.pending_part2) {
    html += `<li class="tl-row">
      <div class="tl-date"><div class="tl-mon">a marcar</div></div>
      <div class="tl-rail"><span class="tl-dot dashed"></span></div>
      <div class="card tl-card pending flex-row items-center" style="flex-direction: row">
        <span class="flex-1 text-sm">Falta marcar a 2ª parte da 1ª consulta</span>
        <button type="button" class="btn" id="part2-open">Marcar</button>
      </div></li>`;
  }
  $("consultas-list").innerHTML = html;
  $("consultas-empty").classList.toggle("hidden", list.length > 0 || !!CONSULTAS.pending_part2);
  if ($("part2-open")) $("part2-open").onclick = () => openApptSheet(CONSULTAS.pending_part2);
  document.querySelectorAll("[data-first]").forEach((sw) => {
    sw.onclick = () => withBusy(sw, async () => {
      const first = sw.getAttribute("aria-checked") !== "true";
      const r = await postJson(`/api/atendente/consulta/${encodeURIComponent(sw.dataset.first)}/primeira`, { phone: PHONE, first, agent: AGENT });
      if (!r.ok) { flash(errText(await r.json().catch(() => ({})), "Não foi possível salvar."), 3000); return; }
      sw.setAttribute("aria-checked", String(first));
      const a = CONSULTAS.appointments.find((x) => x.appointment_id === sw.dataset.first);
      if (a) a.consultation_type = first ? "primeira_consulta" : "acompanhamento";
      flash("Etiqueta salva ✓");
    });
  });
}
```

Ligações:
- Em `loadPatient`, depois de `const { patient, ... } = await r.json();` e do guarda de sequência, fazer `CURRENT_PATIENT = patient;` e chamar `loadConsultas(pid);`.
- Em `selectPatient`, no ramo sem paciente, `CURRENT_PATIENT = null; CONSULTAS = { appointments: [], pending_part2: null, has_completed: false }; renderConsultas();`.

- [ ] **Step 5: Rodar**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/atendente.html dashboard/tests/test_main_auth.py
git commit -m "feat(painel): aba Consultas com linha do tempo e pendência da 2ª parte"
```

---

### Task 14: Painel, folha "Nova consulta"

Folha lateral igual à de vínculo. Campos: médico (segmentado), modalidade (segmentado, a opção fora de `modality_restriction` fica desabilitada), data, hora, duração (1h e 2h; 40min só com a Dra. Bruna), interruptor "1ª consulta" (só para menor; pré-marcado se `has_completed` for falso), escolha "2h seguidas / dividir em dois momentos" (só quando menor + 1ª consulta + Dr. Júlio), segunda caixa de data/hora opcional, observação, cobrança (Normal / Taxa isenta / Cortesia, pré-marcada pela ficha). Rodapé: destinatário ("Carla · final 8888"), link "ver mensagem" que chama `dry_run`, e o botão principal. No 409, aviso azul com os motivos e botão azul "Confirmar encaixe". Quando aberta por "Marcar" (`split_of`), trava médico em Dr. Júlio, duração 1h, 1ª consulta ligada, esconde cobrança e divisão.

**Files:**
- Modify: `dashboard/templates/atendente.html`

- [ ] **Step 1: Marcação da folha**

Depois de `#link-sheet`:

```html
  <div id="appt-sheet" class="hidden">
    <div class="scrim" data-close-appt></div>
    <aside class="sheet" role="dialog" aria-modal="true" aria-labelledby="appt-title">
      <div class="flex items-start gap-3" style="padding: 24px 28px 18px; border-bottom: 1px solid var(--soft)">
        <div class="flex-1 flex flex-col gap-1">
          <span id="appt-cap" class="cap">Nova consulta</span>
          <h2 id="appt-title" class="serif" style="font-size: 26px; margin: 0"></h2>
        </div>
        <button type="button" class="ibtn" data-close-appt aria-label="Fechar">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>
        </button>
      </div>
      <div class="flex-1 overflow-auto flex flex-col gap-5" style="padding: 22px 28px">
        <div class="flex flex-col gap-2"><span class="cap">Médico</span>
          <div id="appt-doctor" class="seg" role="radiogroup" aria-label="Médico">
            <button type="button" role="radio" data-v="julio">Dr. Júlio</button>
            <button type="button" role="radio" data-v="bruna">Dra. Bruna</button>
          </div></div>
        <div class="flex flex-col gap-2"><span class="cap">Modalidade</span>
          <div id="appt-modality" class="seg" role="radiogroup" aria-label="Modalidade">
            <button type="button" role="radio" data-v="presencial">Presencial</button>
            <button type="button" role="radio" data-v="online">Online</button>
          </div></div>
        <div id="appt-first-wrap" class="card flex items-center gap-3" style="padding: 12px 14px">
          <span class="flex-1 text-sm">1ª consulta</span>
          <button id="appt-first" type="button" class="switch" role="switch" aria-checked="false" aria-label="1ª consulta"></button>
        </div>
        <div id="appt-split-wrap" class="hidden flex flex-col gap-2"><span class="cap">Formato</span>
          <div id="appt-split" class="seg" role="radiogroup" aria-label="Formato da 1ª consulta">
            <button type="button" role="radio" data-v="junto">2h seguidas</button>
            <button type="button" role="radio" data-v="dividir">Dois momentos</button>
          </div></div>
        <div class="grid grid-cols-2 gap-3">
          <label class="flex flex-col gap-2"><span id="appt-p1-cap" class="cap">Data</span><input id="appt-date" type="date" class="inp num"></label>
          <label class="flex flex-col gap-2"><span class="cap">Hora</span><input id="appt-time" type="time" step="600" class="inp num"></label>
        </div>
        <div id="appt-dur-wrap" class="flex flex-col gap-2"><span class="cap">Duração</span>
          <div id="appt-dur" class="seg" role="radiogroup" aria-label="Duração">
            <button type="button" role="radio" data-v="60">1h</button>
            <button type="button" role="radio" data-v="120">2h</button>
            <button type="button" role="radio" data-v="40">40min</button>
          </div></div>
        <div id="appt-p2" class="hidden card p-4 flex flex-col gap-3">
          <span class="cap">2ª parte · 1h · pode ficar para depois</span>
          <div class="grid grid-cols-2 gap-3">
            <input id="appt-date2" type="date" class="inp num" aria-label="Data da 2ª parte">
            <input id="appt-time2" type="time" step="600" class="inp num" aria-label="Hora da 2ª parte">
          </div>
        </div>
        <label class="flex flex-col gap-2"><span class="cap">Observação</span>
          <input id="appt-note" class="inp" maxlength="60" placeholder="ex.: Domiciliar"></label>
        <div id="appt-billing-wrap" class="flex flex-col gap-2"><span class="cap">Cobrança</span>
          <div id="appt-billing" class="seg" role="radiogroup" aria-label="Cobrança">
            <button type="button" role="radio" data-v="normal">Normal</button>
            <button type="button" role="radio" data-v="taxa_isenta">Taxa isenta</button>
            <button type="button" role="radio" data-v="cortesia">Cortesia</button>
          </div></div>
        <div id="appt-encaixe" class="hidden notice notice-info" role="alert"></div>
        <p id="appt-error" class="hidden text-sm" style="color: var(--danger)" role="alert"></p>
      </div>
      <div class="flex flex-col gap-3" style="padding: 14px 28px 22px; border-top: 1px solid var(--soft)">
        <div class="flex items-center gap-2 text-sm">
          <span style="color: var(--muted)">Mensagem para</span>
          <span id="appt-to" class="flex-1 font-medium"></span>
          <button id="appt-preview-btn" type="button" class="text-sm" style="color: var(--accent); background: none; border: 0; cursor: pointer">ver mensagem</button>
        </div>
        <pre id="appt-preview" class="hidden text-sm card" style="white-space: pre-wrap; font: inherit; padding: 12px 14px; margin: 0; max-height: 200px; overflow: auto"></pre>
        <div class="flex justify-end">
          <button id="appt-submit" type="button" class="btn btn-primary">Agendar</button>
        </div>
      </div>
    </aside>
  </div>
```

- [ ] **Step 2: JS da folha**

```js
// ── 7. Folha de nova consulta ────────────────────────────────────────────────
let APPT = null;                 // estado do formulário
let APPT_RETURN_FOCUS = null;
let APPT_ENCAIXE = false;        // true depois de um 409: próximo clique confirma encaixe

function segSet(id, v) {
  document.querySelectorAll(`#${id} [role=radio]`).forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === v)));
}
function segGet(id) {
  const b = document.querySelector(`#${id} [aria-checked="true"]`);
  return b ? b.dataset.v : null;
}
function segDisable(id, v, off) {
  const b = document.querySelector(`#${id} [data-v="${v}"]`);
  if (b) { b.disabled = off; b.classList.toggle("hidden", off && b.dataset.hideWhenOff === "1"); }
}
["appt-doctor", "appt-modality", "appt-split", "appt-dur", "appt-billing"].forEach((id) => {
  document.querySelectorAll(`#${id} [role=radio]`).forEach((b) => {
    b.onclick = () => { if (!b.disabled) { segSet(id, b.dataset.v); apptChanged(); } };
  });
});
document.querySelector('#appt-dur [data-v="40"]').dataset.hideWhenOff = "1";
$("appt-first").onclick = () => {
  const sw = $("appt-first");
  sw.setAttribute("aria-checked", String(sw.getAttribute("aria-checked") !== "true"));
  apptChanged();
};
["appt-date", "appt-time", "appt-date2", "appt-time2", "appt-note"].forEach((id) => { $(id).oninput = apptChanged; });

function apptBody(extra = {}) {
  const split = APPT.splitOf ? false : segGet("appt-split") === "dividir" && !$("appt-split-wrap").classList.contains("hidden");
  const parts = [{ start: `${$("appt-date").value}T${$("appt-time").value}`, minutes: Number(segGet("appt-dur")) }];
  if (split && $("appt-date2").value && $("appt-time2").value) parts.push({ start: `${$("appt-date2").value}T${$("appt-time2").value}`, minutes: 60 });
  return {
    phone: PHONE, patient_id: CURRENT_PID, doctor: segGet("appt-doctor"), modality: segGet("appt-modality"),
    parts, split, split_of: APPT.splitOf, session_note: $("appt-note").value.trim(),
    first_consultation: APPT.splitOf ? true : ($("appt-first-wrap").classList.contains("hidden") ? false : $("appt-first").getAttribute("aria-checked") === "true"),
    billing: segGet("appt-billing") || "normal", encaixe_confirmed: false, dry_run: false, agent: AGENT, ...extra,
  };
}

function apptChanged() {
  // Qualquer mudança desfaz o aviso de encaixe: o horário novo precisa ser checado de novo.
  APPT_ENCAIXE = false;
  $("appt-encaixe").classList.add("hidden");
  $("appt-preview").classList.add("hidden");
  $("appt-error").classList.add("hidden");
  const doctor = segGet("appt-doctor");
  const minor = isMinor(CURRENT_PATIENT, $("appt-date").value || todayIso());
  $("appt-first-wrap").classList.toggle("hidden", !minor || !!APPT.splitOf);
  const first = minor && $("appt-first").getAttribute("aria-checked") === "true";
  const canSplit = !APPT.splitOf && first && doctor === "julio";
  $("appt-split-wrap").classList.toggle("hidden", !canSplit);
  const split = canSplit && segGet("appt-split") === "dividir";
  $("appt-p2").classList.toggle("hidden", !split);
  $("appt-p1-cap").textContent = split ? "Data da 1ª parte" : "Data";
  $("appt-dur-wrap").classList.toggle("hidden", split || !!APPT.splitOf);
  if (split || APPT.splitOf) segSet("appt-dur", "60");
  segDisable("appt-dur", "40", doctor !== "bruna");
  if (doctor !== "bruna" && segGet("appt-dur") === "40") segSet("appt-dur", "60");
  const ok = $("appt-date").value && $("appt-time").value && segGet("appt-modality") && doctor;
  $("appt-submit").disabled = !ok;
  $("appt-submit").className = "btn btn-primary";
  $("appt-submit").textContent = APPT.splitOf ? "Marcar 2ª parte" : "Agendar";
}

function openApptSheet(splitOf) {
  const p = CURRENT_PATIENT;
  if (!p) return;
  APPT = { splitOf };
  $("appt-title").textContent = p.name || "";
  $("appt-cap").textContent = splitOf ? "2ª parte da 1ª consulta" : "Nova consulta";
  const docKey = splitOf ? "julio" : (DOCTOR_KEYS[p.doctor_id] || "julio");
  segSet("appt-doctor", docKey);
  document.querySelectorAll("#appt-doctor [role=radio]").forEach((b) => { b.disabled = !!splitOf && b.dataset.v !== "julio"; });
  const restr = p.modality_restriction;
  segSet("appt-modality", restr || "presencial");
  segDisable("appt-modality", "online", restr === "presencial");
  segDisable("appt-modality", "presencial", restr === "online");
  $("appt-first").setAttribute("aria-checked", String(!CONSULTAS.has_completed));
  segSet("appt-split", "junto");
  segSet("appt-dur", !CONSULTAS.has_completed && isMinor(p) && docKey === "julio" ? "120" : "60");
  segSet("appt-billing", p.custom_price === 0 ? "cortesia" : p.booking_fee_waived ? "taxa_isenta" : "normal");
  $("appt-billing-wrap").classList.toggle("hidden", !!splitOf);
  ["appt-date", "appt-time", "appt-date2", "appt-time2", "appt-note"].forEach((id) => { $(id).value = ""; });
  $("appt-to").textContent = "…";
  apptChanged();
  APPT_RETURN_FOCUS = document.activeElement;
  document.querySelector(".eva > .max-w-5xl").inert = true;
  $("appt-sheet").classList.remove("hidden");
  $("appt-date").focus();
}
function closeApptSheet() {
  $("appt-sheet").classList.add("hidden");
  document.querySelector(".eva > .max-w-5xl").inert = false;
  if (APPT_RETURN_FOCUS && document.body.contains(APPT_RETURN_FOCUS)) APPT_RETURN_FOCUS.focus();
  APPT_RETURN_FOCUS = null;
}
document.querySelectorAll("[data-close-appt]").forEach((el) => { el.onclick = closeApptSheet; });

function apptError(msg) {
  $("appt-error").textContent = msg;
  $("appt-error").classList.remove("hidden");
}

$("appt-preview-btn").onclick = () => withBusy($("appt-preview-btn"), async () => {
  if ($("appt-submit").disabled) { apptError("Preencha data, hora e modalidade."); return; }
  const r = await postJson("/api/atendente/consulta/nova", apptBody({ dry_run: true }));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { apptError(errText(body, "Não foi possível montar a prévia.")); return; }
  const m = body.message;
  $("appt-to").textContent = m.recipients.length
    ? m.recipients.map((c) => `${c.name} · final ${c.phone_hint}`).join(", ")
    : "ninguém (sem número liberado)";
  $("appt-preview").textContent = m.text + (m.held.length ? `\n\n(Eva desligada para: ${m.held.join(", ")}; não recebe)` : "");
  $("appt-preview").classList.remove("hidden");
});

$("appt-submit").onclick = () => withBusy($("appt-submit"), async () => {
  $("appt-error").classList.add("hidden");
  const r = await postJson("/api/atendente/consulta/nova", apptBody({ encaixe_confirmed: APPT_ENCAIXE }));
  const body = await r.json().catch(() => ({}));
  if (r.status === 409 && body.detail && body.detail.needs_encaixe) {
    APPT_ENCAIXE = true;
    $("appt-encaixe").innerHTML = `<strong>Encaixe.</strong> ${body.detail.reasons.map(escapeHtml).join("; ")}.`;
    $("appt-encaixe").classList.remove("hidden");
    $("appt-submit").className = "btn btn-info";
    $("appt-submit").textContent = "Confirmar encaixe";
    return;
  }
  if (!r.ok) { apptError(errText(body, "Não foi possível agendar. Nada foi alterado.")); return; }
  const m = body.message || {};
  closeApptSheet();
  loadConsultas(CURRENT_PID);
  loadPagamentos();
  if (m.not_delivered && m.not_delivered.length) {
    flash(`Consulta registrada, mas a mensagem não foi entregue a ${m.not_delivered.join(", ")}. Avise o paciente por outro meio.`, 8000);
  } else {
    flash("Consulta registrada e paciente avisado ✓", 3000);
  }
});
```

Atenção ao `apptChanged` dentro do `onclick` do botão "Confirmar encaixe": como o clique não muda campos, `APPT_ENCAIXE` continua verdadeiro e o segundo envio vai com `encaixe_confirmed: true`.

No handler de `keydown` do Escape, antes do teste da `link-sheet`:

```js
  if (!$("appt-sheet").classList.contains("hidden")) { closeApptSheet(); return; }
```

Conferir que `flash(msg, ms)` aceita mensagens longas sem cortar (ler `flash` ~261); se o elemento tiver largura fixa, deixar quebrar linha.

- [ ] **Step 3: Verificação visual**

Subir o preview local com banco falso (o `preview_painel.py` da Parte 1 está no scratchpad desta sessão; se não estiver disponível, criar um igual: FastAPI que monta `attendant_routes` com `attendant_db` apontando para `FakeClient` com dados de exemplo e `eva_client.post` falso que devolve 409 na primeira chamada e 200 na segunda). Conferir no navegador, em 1280px e 375px, claro e escuro:
- aba Consultas abre primeiro, com topo, linha do tempo e pendência tracejada;
- "Nova consulta" abre a folha; 40min só aparece com a Dra. Bruna;
- menor + 1ª consulta + Dr. Júlio mostra "2h seguidas / Dois momentos"; "Dois momentos" abre a 2ª caixa;
- "ver mensagem" mostra destinatário e texto;
- primeiro clique com horário fora da grade mostra o aviso azul e o botão azul; segundo clique agenda;
- "Marcar" da pendência abre a folha travada em Dr. Júlio, 1h, sem cobrança.

- [ ] **Step 4: Rodar os testes**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(painel): folha Nova consulta com encaixe, divisão infantil e cortesia"
```

---

### Task 15: Verificação final, configuração e migração

- [ ] **Step 1: Suítes completas**

Run: `uv run pytest --tb=short -q` e `cd dashboard && uv run pytest -q`
Expected: tudo PASS. Anotar os números para a descrição do PR.

- [ ] **Step 2: Revisão final de código** (subagente revisor sobre o diff inteiro `git diff origin/main...HEAD`).

- [ ] **Step 3: Preparar a produção (com a Ayexa, antes do merge)**

1. Aplicar a migração `supabase/migrations/20260927_appointments_courtesy_session_note.sql` no Supabase de produção (SQL editor, ou script `scripts/_apply_migration_20260927.py` seguindo o padrão de `scripts/_fix_appointments_status_check.py`). Conferir depois com `select is_courtesy, session_note from appointments limit 1`.
2. No Easypanel, serviço do painel: criar `EVA_BASE_URL=https://psiqueai.ayexa.com.br` e `ADMIN_SECRET` com o mesmo valor da Eva.
3. Só então fazer o merge.

- [ ] **Step 4: PR**

Abrir o PR a partir da worktree, com a descrição dizendo: o que muda, a ordem de deploy acima, a limitação da janela de 24h (sem modelo aprovado pela Meta, fora da janela a mensagem não é enviada e o painel avisa), e o plano de teste manual pós-deploy (agendar paciente de teste no horário livre, encaixe fora da grade, 1ª consulta infantil dividida com 2ª parte depois, cortesia; conferir Calendar, `appointments`, `events`, `messages` e que a cobrança de taxa não dispara para a cortesia).
