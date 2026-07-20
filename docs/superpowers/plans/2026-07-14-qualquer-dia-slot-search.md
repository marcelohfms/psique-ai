# "Qualquer dia" Slot Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando o paciente responde "qualquer dia" (ou "tanto faz") à pergunta de dia da semana, a Eva deve buscar e apresentar os horários disponíveis nos próximos dias úteis em vez de repetir a pergunta.

**Architecture:** `get_available_slots` (`app/graph/tools.py`) ganha um novo branch, disparado antes da checagem de "expressão vaga" existente, que varre dias úteis a partir de hoje (semana atual, até 3 dias distintos com vaga), e — se encontrar menos de 2 dias distintos — completa com a semana seguinte inteira; se mesmo assim nada for encontrado, continua expandindo semana a semana (limite de segurança de 8 semanas) até achar algo. Reaproveita a função existente `google_calendar.get_available_slots` (busca de um único dia) chamando-a repetidamente, exatamente como o branch de "dia da semana específico" já faz hoje.

**Tech Stack:** Python, LangGraph tool (`@tool` async function), pytest + `unittest.mock` (AsyncMock/patch), spec de referência: `docs/superpowers/specs/2026-07-14-qualquer-dia-slot-search-design.md`.

---

## Task 1: Escrever os testes (falhando) para o novo comportamento

**Files:**
- Modify: `tests/test_tools.py` (insere novos testes logo após `test_get_available_slots_julio_age_exception_bypasses_over_65`, antes do comentário `# ── confirm_appointment ───`)

- [ ] **Step 1: Adicionar a classe de data congelada e os 5 novos testes**

Insira o bloco abaixo em `tests/test_tools.py` imediatamente antes da linha `# ── confirm_appointment ───────────────────────────────────────────────────────`:

```python
_real_dt = datetime


class _FrozenDTTuesday(_real_dt):
    """'Today' = 2026-07-07, uma terça-feira, com 4 dias úteis restantes nesta
    semana (terça a sexta) e a semana seguinte começando em 13/07 (segunda)."""
    @classmethod
    def now(cls, tz=None):
        return _real_dt(2026, 7, 7, 10, 0, tzinfo=tz) if tz else _real_dt(2026, 7, 7, 10, 0)


# ── get_available_slots — "qualquer dia" (sem preferência de dia) ─────────────

async def test_get_available_slots_qualquer_dia_uses_current_week_when_enough_days():
    """'qualquer dia' com >=2 dias distintos disponíveis nesta semana NÃO deve buscar a semana seguinte."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
        if preferred_shift == "manha" and preferred_day in ("2026-07-07", "2026-07-08"):
            day = int(preferred_day[-2:])
            return [(datetime(2026, 7, day, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "07/07" in result
    assert "08/07" in result
    assert "semana seguinte" not in result.lower()
    assert "outras semanas" not in result.lower()
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert "2026-07-13" not in called_days  # nunca buscou a semana seguinte


async def test_get_available_slots_qualquer_dia_extends_to_next_week_when_few():
    """Menos de 2 dias distintos nesta semana → soma a semana seguinte inteira."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
        if preferred_shift != "manha":
            return []
        if preferred_day == "2026-07-07":  # só terça nesta semana
            return [(datetime(2026, 7, 7, 9, 0, tzinfo=TZ), "escolha")]
        if preferred_day == "2026-07-13":  # segunda da semana seguinte
            return [(datetime(2026, 7, 13, 9, 0, tzinfo=TZ), "escolha")]
        if preferred_day == "2026-07-15":  # quarta da semana seguinte
            return [(datetime(2026, 7, 15, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "07/07" in result
    assert "13/07" in result
    assert "15/07" in result
    assert "outras semanas" in result.lower()


async def test_get_available_slots_qualquer_dia_keeps_expanding_until_found():
    """Duas semanas totalmente vazias NUNCA devem gerar mensagem de 'não encontrei' —
    a busca deve continuar expandindo até achar algo."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
        if preferred_shift == "manha" and preferred_day == "2026-07-20":  # 3ª semana, segunda
            return [(datetime(2026, 7, 20, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "20/07" in result
    assert "não encontrei" not in result.lower()


async def test_get_available_slots_qualquer_dia_e_qualquer_turno_shows_per_shift_breakdown():
    """'qualquer dia' combinado com turno 'qualquer' (o caso real mais comum, já
    que a Eva pergunta o dia antes do turno) deve mostrar o detalhamento por turno."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
        if preferred_day == "2026-07-07" and preferred_shift == "tarde":
            return [(datetime(2026, 7, 7, 14, 0, tzinfo=TZ), "escolha")]
        if preferred_day == "2026-07-08" and preferred_shift == "manha":
            return [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "Tarde: 14:00" in result
    assert "Manhã: 09:00" in result


async def test_get_available_slots_semana_que_vem_still_asks_clarification():
    """Regressão: separar 'qualquer'/'tanto faz' de _vague_patterns não pode quebrar
    o fluxo de esclarecimento para 'semana que vem' (sem dia informado)."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="semana que vem",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "CLARIFICAÇÃO NECESSÁRIA" in result
    mock_slots.assert_not_called()
```

- [ ] **Step 2: Rodar os novos testes e confirmar que falham**

Run: `uv run pytest tests/test_tools.py -k qualquer_dia -v`
Expected: os 4 testes de "qualquer_dia" falham (hoje `preferred_day="qualquer dia"` cai no branch de `CLARIFICAÇÃO NECESSÁRIA`, então as asserções de conteúdo tipo `"07/07" in result` falham). O teste `test_get_available_slots_semana_que_vem_still_asks_clarification` deve passar (comportamento já existente, ainda intacto) — rode-o à parte para confirmar:

Run: `uv run pytest tests/test_tools.py -k semana_que_vem -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_tools.py
git commit -m "test(tools): add coverage for 'qualquer dia' slot search"
```

---

## Task 2: Implementar a busca "qualquer dia" em `app/graph/tools.py`

**Files:**
- Modify: `app/graph/tools.py:3` (import)
- Modify: `app/graph/tools.py:94-97` (novas funções auxiliares, antes de `@tool async def get_available_slots`)
- Modify: `app/graph/tools.py:161-238` (branch da tool `get_available_slots`)

- [ ] **Step 1: Adicionar `date` ao import de `datetime`**

Em `app/graph/tools.py:3`, troque:

```python
from datetime import datetime, timedelta
```

por:

```python
from datetime import datetime, timedelta, date
```

- [ ] **Step 2: Adicionar as funções auxiliares de busca "qualquer dia"**

Em `app/graph/tools.py`, logo depois do dicionário `_MOD_LABELS` (linha 94, antes de `@tool` / `async def get_available_slots` na linha 97), insira:

```python
_ANY_DAY_MAX_DAYS_CURRENT_WEEK = 3
_ANY_DAY_MIN_DISTINCT_DAYS = 2
_ANY_DAY_MAX_WEEKS = 8


def _week_range(offset_weeks: int) -> tuple[date, date]:
    """Retorna (início, fim) da janela de busca: offset_weeks=0 é "esta semana"
    (de hoje até domingo); offset_weeks>=1 é uma semana cheia (segunda a
    domingo), offset_weeks semanas após a atual."""
    today = datetime.now(TZ).date()
    if offset_weeks == 0:
        start = today
    else:
        next_monday = today + timedelta(days=7 - today.weekday())
        start = next_monday + timedelta(weeks=offset_weeks - 1)
    end = start + timedelta(days=6 - start.weekday())
    return start, end


def _business_days(start: date, end: date):
    """Percorre cada dia útil (segunda a sexta) de start a end, inclusive."""
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


async def _slots_for_any_day(
    day: date, calendar_id: str, doctor: str, preferred_shift: str,
    slot_duration_minutes: int, _get_slots,
) -> dict:
    """Retorna {turno: slots} para o dia informado. Consulta apenas o turno
    pedido, ou os três turnos quando preferred_shift == "qualquer"."""
    if preferred_shift != "qualquer":
        slots = await _get_slots(
            calendar_id=calendar_id,
            preferred_day=day.isoformat(),
            preferred_shift=preferred_shift,
            slot_minutes=slot_duration_minutes,
            doctor_key=doctor,
        )
        return {preferred_shift: slots} if slots else {}
    result: dict = {}
    for shift_key in ("manha", "tarde", "noite"):
        slots = await _get_slots(
            calendar_id=calendar_id,
            preferred_day=day.isoformat(),
            preferred_shift=shift_key,
            slot_minutes=slot_duration_minutes,
            doctor_key=doctor,
        )
        if slots:
            result[shift_key] = slots
    return result


def _format_any_day_section(day: date, day_shifts: dict, preferred_shift: str) -> str:
    day_label = _WEEKDAY_LABELS_PT.get(day.weekday(), "")
    date_label = day.strftime("%d/%m")
    header = f"{day_label}, dia {date_label}" if day_label else date_label
    if preferred_shift == "qualquer":
        lines = [f"{header}:"]
        for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
            slots = day_shifts.get(shift_key)
            if slots:
                times = ", ".join(s[0].strftime("%H:%M") for s in slots)
                lines.append(f"  - {shift_label.capitalize()}: {times}")
        return "\n".join(lines)
    slots = day_shifts.get(preferred_shift, [])
    lines = [f"{header} ({preferred_shift}):"]
    for i, (slot, modality) in enumerate(slots, 1):
        lines.append(f"  {i}. {slot.strftime('%H:%M')} [{_MOD_LABELS.get(modality, modality)}]")
    return "\n".join(lines)


async def _search_any_day(calendar_id: str, doctor: str, preferred_shift: str, slot_duration_minutes: int) -> str:
    """Busca dias úteis futuros (qualquer dia da semana) quando o paciente não
    tem preferência de dia (ex: "qualquer dia"). Busca primeiro a semana atual
    (até 3 dias distintos com vaga); se encontrar menos de 2 dias distintos,
    também busca a semana seguinte inteira; se ainda assim não achar nada,
    continua expandindo semana a semana (limite de segurança) até achar algo —
    nunca informa ao paciente que "não encontrou"."""
    from app.google_calendar import get_available_slots as _get_slots

    found: list[tuple[date, dict]] = []

    start, end = _week_range(0)
    for day in _business_days(start, end):
        day_shifts = await _slots_for_any_day(
            day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots
        )
        if day_shifts:
            found.append((day, day_shifts))
            if len(found) >= _ANY_DAY_MAX_DAYS_CURRENT_WEEK:
                break

    extended = False
    if len(found) < _ANY_DAY_MIN_DISTINCT_DAYS:
        extended = True
        start, end = _week_range(1)
        for day in _business_days(start, end):
            day_shifts = await _slots_for_any_day(
                day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots
            )
            if day_shifts:
                found.append((day, day_shifts))

    week_offset = 2
    while not found and week_offset <= _ANY_DAY_MAX_WEEKS:
        extended = True
        start, end = _week_range(week_offset)
        for day in _business_days(start, end):
            day_shifts = await _slots_for_any_day(
                day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots
            )
            if day_shifts:
                found.append((day, day_shifts))
        week_offset += 1

    if not found:
        return (
            "Não encontrei horários disponíveis nas próximas semanas. "
            "Use transfer_to_human para encaminhar ao atendente humano verificar outras opções."
        )

    sections = [_format_any_day_section(day, day_shifts, preferred_shift) for day, day_shifts in found]
    prefix = (
        "Poucos horários disponíveis na semana atual — incluí também outras semanas:\n\n"
        if extended else ""
    )
    return prefix + "\n\n".join(sections)
```

- [ ] **Step 3: Religar o branch dentro de `get_available_slots`**

Em `app/graph/tools.py`, o trecho atual (linhas 161–238) é:

```python
    # ── "qualquer" shift: check all shifts and return summary ─────────────────
    if preferred_shift == "qualquer":
        # Detect whether preferred_day is a weekday name so we can do multi-week
        # search — the same logic used below for specific shifts.
        preferred_day_norm_q = preferred_day.lower().strip()
        weekday_key_q = next(
            (wd for name, wd in _WEEKDAYS_PT.items() if name in preferred_day_norm_q),
            None,
        )
        base_date_q = _parse_day(preferred_day)
        if base_date_q is None:
            return "Não entendi a data. Por favor informe um dia específico (ex: segunda, 19/05, amanhã)."

        # For weekday names: try up to 4 weeks until we find a date with slots.
        # For specific dates: single attempt only.
        max_weeks = 4 if weekday_key_q is not None else 1
        for week_offset in range(max_weeks):
            try_date = base_date_q + timedelta(weeks=week_offset)
            day_of_week = _WEEKDAY_LABELS_PT.get(try_date.weekday(), "")
            date_label = try_date.strftime("%d/%m")
            header = f"{day_of_week}, dia {date_label}" if day_of_week else date_label
            sections = []
            for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
                slots = await _get_slots(
                    calendar_id=calendar_id,
                    preferred_day=try_date.isoformat(),
                    preferred_shift=shift_key,
                    slot_minutes=slot_duration_minutes,
                    doctor_key=doctor,
                )
                logger.info("GET_SLOTS_RESULT date=%s shift=%s slots=%s", try_date, shift_key, [s[0].strftime("%H:%M") for s in slots])
                if slots:
                    times = ", ".join(s[0].strftime("%H:%M") for s in slots)
                    sections.append(f"- {shift_label.capitalize()}: {times}")
            if sections:
                return f"Horários disponíveis para {header}:\n" + "\n".join(sections)
            # No 2h blocks found — check if there are 1h slots (non-consecutive case)
            if slot_duration_minutes == 120:
                single_sections = []
                for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
                    slots_1h = await _get_slots(
                        calendar_id=calendar_id,
                        preferred_day=try_date.isoformat(),
                        preferred_shift=shift_key,
                        slot_minutes=60,
                        doctor_key=doctor,
                    )
                    if slots_1h:
                        times = ", ".join(s[0].strftime("%H:%M") for s in slots_1h)
                        single_sections.append(f"- {shift_label.capitalize()}: {times}")
                if single_sections:
                    return (
                        f"Há horários disponíveis em {header}, mas não em bloco de 2 horas seguidas:\n"
                        + "\n".join(single_sections)
                        + "\nInforme o paciente que não há 2 horas consecutivas disponíveis neste dia. "
                        "Pergunte se prefere verificar outro dia com 2 horas consecutivas disponíveis."
                    )
            # No slots at all this week — try the next occurrence (only for weekday names)
        return f"Não há horários disponíveis para {header}. Deseja tentar outro dia?"

    now = datetime.now(TZ)
    shift_norm = preferred_shift.lower().replace("ã", "a").replace("manhã", "manha").strip()
    shift_start_h, shift_end_h = SHIFT_HOURS.get(shift_norm, (8, 18))

    # ── Detect weekday name → multi-week search ───────────────────────────────
    preferred_day_norm = preferred_day.lower().strip()
    weekday_key = next(
        (wd for name, wd in _WEEKDAYS_PT.items() if name in preferred_day_norm),
        None,
    )

    # ── Vague expressions (e.g. "próxima semana") → ask for specific day ─────
    _vague_patterns = ("semana", "mês", "mes", "em breve", "qualquer", "tanto faz")
    if weekday_key is None and any(p in preferred_day_norm for p in _vague_patterns):
        return (
            "CLARIFICAÇÃO NECESSÁRIA: O paciente disse uma expressão vaga (ex: 'próxima semana'). "
            "Pergunte qual dia da semana prefere (segunda a sexta) antes de chamar get_available_slots novamente."
        )
```

Substitua por (reordena a detecção de `weekday_key` para antes do branch de turno "qualquer" — necessário porque hoje um paciente que combina "qualquer dia" com turno ainda desconhecido cairia no branch de turno "qualquer" primeiro e receberia "Não entendi a data"; remove a duplicação de `preferred_day_norm_q`/`weekday_key_q` reaproveitando as variáveis já calculadas; e reduz `_vague_patterns` para não incluir mais "qualquer"/"tanto faz", que agora têm branch próprio):

```python
    now = datetime.now(TZ)
    shift_norm = preferred_shift.lower().replace("ã", "a").replace("manhã", "manha").strip()
    shift_start_h, shift_end_h = SHIFT_HOURS.get(shift_norm, (8, 18))

    # ── Detect weekday name so the branches below only run for real day values ─
    preferred_day_norm = preferred_day.lower().strip()
    weekday_key = next(
        (wd for name, wd in _WEEKDAYS_PT.items() if name in preferred_day_norm),
        None,
    )

    # ── No day preference (e.g. "qualquer dia", "tanto faz") → search upcoming
    # business days regardless of weekday, expanding to later weeks if needed ──
    _no_day_pref_patterns = ("qualquer", "tanto faz")
    if weekday_key is None and any(p in preferred_day_norm for p in _no_day_pref_patterns):
        return await _search_any_day(
            calendar_id=calendar_id,
            doctor=doctor,
            preferred_shift=preferred_shift,
            slot_duration_minutes=slot_duration_minutes,
        )

    # ── "qualquer" shift: check all shifts and return summary ─────────────────
    if preferred_shift == "qualquer":
        base_date_q = _parse_day(preferred_day)
        if base_date_q is None:
            return "Não entendi a data. Por favor informe um dia específico (ex: segunda, 19/05, amanhã)."

        # For weekday names: try up to 4 weeks until we find a date with slots.
        # For specific dates: single attempt only.
        max_weeks = 4 if weekday_key is not None else 1
        for week_offset in range(max_weeks):
            try_date = base_date_q + timedelta(weeks=week_offset)
            day_of_week = _WEEKDAY_LABELS_PT.get(try_date.weekday(), "")
            date_label = try_date.strftime("%d/%m")
            header = f"{day_of_week}, dia {date_label}" if day_of_week else date_label
            sections = []
            for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
                slots = await _get_slots(
                    calendar_id=calendar_id,
                    preferred_day=try_date.isoformat(),
                    preferred_shift=shift_key,
                    slot_minutes=slot_duration_minutes,
                    doctor_key=doctor,
                )
                logger.info("GET_SLOTS_RESULT date=%s shift=%s slots=%s", try_date, shift_key, [s[0].strftime("%H:%M") for s in slots])
                if slots:
                    times = ", ".join(s[0].strftime("%H:%M") for s in slots)
                    sections.append(f"- {shift_label.capitalize()}: {times}")
            if sections:
                return f"Horários disponíveis para {header}:\n" + "\n".join(sections)
            # No 2h blocks found — check if there are 1h slots (non-consecutive case)
            if slot_duration_minutes == 120:
                single_sections = []
                for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
                    slots_1h = await _get_slots(
                        calendar_id=calendar_id,
                        preferred_day=try_date.isoformat(),
                        preferred_shift=shift_key,
                        slot_minutes=60,
                        doctor_key=doctor,
                    )
                    if slots_1h:
                        times = ", ".join(s[0].strftime("%H:%M") for s in slots_1h)
                        single_sections.append(f"- {shift_label.capitalize()}: {times}")
                if single_sections:
                    return (
                        f"Há horários disponíveis em {header}, mas não em bloco de 2 horas seguidas:\n"
                        + "\n".join(single_sections)
                        + "\nInforme o paciente que não há 2 horas consecutivas disponíveis neste dia. "
                        "Pergunte se prefere verificar outro dia com 2 horas consecutivas disponíveis."
                    )
            # No slots at all this week — try the next occurrence (only for weekday names)
        return f"Não há horários disponíveis para {header}. Deseja tentar outro dia?"

    # ── Vague expressions (e.g. "próxima semana") → ask for specific day ─────
    _vague_patterns = ("semana", "mês", "mes", "em breve")
    if weekday_key is None and any(p in preferred_day_norm for p in _vague_patterns):
        return (
            "CLARIFICAÇÃO NECESSÁRIA: O paciente disse uma expressão vaga (ex: 'próxima semana'). "
            "Pergunte qual dia da semana prefere (segunda a sexta) antes de chamar get_available_slots novamente."
        )
```

- [ ] **Step 4: Rodar os testes novos e confirmar que passam**

Run: `uv run pytest tests/test_tools.py -k "qualquer_dia or semana_que_vem" -v`
Expected: todos os 5 testes PASSAM.

- [ ] **Step 5: Rodar a suíte completa e confirmar que não há regressão**

Run: `uv run pytest --tb=short`
Expected: todos os testes passam (nenhuma quebra em `test_get_available_slots_*` pré-existentes, `test_calendar.py`, `test_process_message.py`, etc.)

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py
git commit -m "feat(tools): search upcoming business days when patient has no day preference"
```

---

## Task 3: Atualizar as instruções da Eva em `app/graph/prompts.py`

**Files:**
- Modify: `app/graph/prompts.py:846-848` (bloco `EXISTING_PATIENT_SYSTEM`)
- Modify: `app/graph/prompts.py:997-999` (bloco `NEW_PATIENT_SYSTEM`)

- [ ] **Step 1: Adicionar a instrução no bloco `EXISTING_PATIENT_SYSTEM`**

Em `app/graph/prompts.py`, localize (dentro de `EXISTING_PATIENT_SYSTEM`, por volta da linha 846):

```python
- Se o paciente disser "próxima semana", "semana que vem", "semana seguinte" ou expressão vaga similar sem especificar um dia, consulte os HORÁRIOS DE ATENDIMENTO acima e pergunte qual dia prefere entre os dias em que o médico realmente atende (ex: se o médico atende segunda, quarta e sexta, ofereça apenas esses dias) ANTES de chamar get_available_slots.
```

Adicione a linha abaixo **imediatamente antes** dela:

```python
- Se o paciente disser "qualquer dia", "tanto faz", "não tenho preferência de dia" ou expressão equivalente indicando que não tem preferência de dia da semana, chame get_available_slots passando preferred_day="qualquer dia" diretamente — NUNCA pergunte qual dia da semana ele prefere nesse caso. A ferramenta busca automaticamente os próximos dias úteis disponíveis e, se necessário, também semanas seguintes.
- Se o paciente disser "próxima semana", "semana que vem", "semana seguinte" ou expressão vaga similar sem especificar um dia, consulte os HORÁRIOS DE ATENDIMENTO acima e pergunte qual dia prefere entre os dias em que o médico realmente atende (ex: se o médico atende segunda, quarta e sexta, ofereça apenas esses dias) ANTES de chamar get_available_slots.
```

- [ ] **Step 2: Adicionar a mesma instrução no bloco `NEW_PATIENT_SYSTEM`**

Em `app/graph/prompts.py`, localize (dentro de `NEW_PATIENT_SYSTEM`, por volta da linha 997) o mesmo texto:

```python
- Se o paciente disser "próxima semana", "semana que vem", "semana seguinte" ou expressão vaga similar sem especificar um dia, consulte os HORÁRIOS DE ATENDIMENTO acima e pergunte qual dia prefere entre os dias em que o médico realmente atende (ex: se o médico atende segunda, quarta e sexta, ofereça apenas esses dias) ANTES de chamar get_available_slots.
```

(essa string aparece duas vezes no arquivo — use o `replace_all` da ferramenta de edição, ou edite manualmente a segunda ocorrência, a que está dentro de `NEW_PATIENT_SYSTEM`) e adicione a mesma linha nova imediatamente antes dela:

```python
- Se o paciente disser "qualquer dia", "tanto faz", "não tenho preferência de dia" ou expressão equivalente indicando que não tem preferência de dia da semana, chame get_available_slots passando preferred_day="qualquer dia" diretamente — NUNCA pergunte qual dia da semana ele prefere nesse caso. A ferramenta busca automaticamente os próximos dias úteis disponíveis e, se necessário, também semanas seguintes.
```

- [ ] **Step 3: Rodar a suíte completa novamente**

Run: `uv run pytest --tb=short`
Expected: todos os testes continuam passando (as strings de prompt não são verificadas por teste algum hoje, então não deve haver nenhuma quebra).

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "docs(prompts): instruct Eva to search broadly when patient has no day preference"
```
