# Expressão vaga de dia → relação de horários — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando o paciente for vago sobre o dia da consulta ("próxima semana", "essa semana", "em breve"), a Eva oferece a relação de horários daquela janela em vez de perguntar qual dia ele prefere.

**Architecture:** Um novo helper `_search_week(week_offset)` em `app/graph/tools.py` varre os dias úteis de uma semana específica e devolve todos os dias com vaga (reusando `_slots_for_any_day` e `_format_any_day_section`). Um bloco de roteamento em `_get_available_slots_impl`, inserido **antes** do branch `preferred_shift == "qualquer"`, direciona expressões de semana para `_search_week` (com fallback para `_search_any_day`). O prompt deixa de mandar a Eva perguntar o dia.

**Tech Stack:** Python, LangChain `@tool`, pytest (async), unittest.mock.

---

## Contexto do código (leia antes de começar)

Arquivo principal: `app/graph/tools.py`.

Helpers **já existentes** que serão reusados (não recriar):
- `_week_range(offset_weeks)` (linha ~252): `offset_weeks=0` → `(hoje, domingo desta semana)`; `offset_weeks=1` → `(segunda, domingo da semana seguinte)`.
- `_business_days(start, end)` (linha ~266): gera dias úteis (seg–sex) no intervalo, inclusive.
- `_prefetch_supabase_busy(doctor, first_day, last_day)` (linha ~275): busca as faixas ocupadas UMA vez; fail-open (retorna `None`).
- `_slots_for_any_day(day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots, supabase_busy=None)` (linha ~296): retorna `{turno: slots}` do dia; consulta só o turno pedido, ou os três se `preferred_shift == "qualquer"`.
- `_format_any_day_section(day, day_shifts, preferred_shift)` (linha ~330): formata uma seção por dia.
- `_search_any_day(calendar_id, doctor, preferred_shift, slot_duration_minutes)` (linha ~504): fallback "próximos dias com vaga".

**Ordem atual dos branches** em `_get_available_slots_impl` (linhas ~669-762):
1. mês inteiro (`is_month_only`)
2. sem preferência de dia (`_no_day_pref_patterns = ("qualquer", "tanto faz")`) → `_search_any_day`
3. `if preferred_shift == "qualquer":` → chama `_parse_day(preferred_day)`; se `None` → devolve **"Não entendi a data…"**
4. `_vague_patterns = ("semana", "em breve")` → devolve **"CLARIFICAÇÃO NECESSÁRIA…"**

⚠️ **Armadilha:** o branch 3 roda antes do 4. Para "próxima semana" com `preferred_shift="qualquer"` (caso comum: paciente não citou turno), `_parse_day` devolve `None` e o branch 3 responde "Não entendi a data" — o branch 4 nunca dispara. Por isso o novo roteamento entra **entre o branch 2 e o branch 3**, e o branch 4 antigo é removido (vira código morto).

Teste que **conflita** e será reescrito: `tests/test_tools.py::test_get_available_slots_semana_que_vem_still_asks_clarification` (linha ~788) hoje afirma que "semana que vem" → CLARIFICAÇÃO.

Testes que **permanecem verdes** (não mexer): `test_get_available_slots_..._esse_mes` (linha ~558, usa `_parse_day` no fim da função, não o branch vago) e os de `tests/test_calendar.py` sobre `_parse_day("quarta da próxima semana")` (dia específico, `weekday_key` não é `None`).

Fixture de teste: `_FrozenDTTuesday` (linha ~299) congela "hoje" em **terça, 2026-07-07 10:00**. Semana atual restante = ter/qua/qui/sex (07–10/07). Semana seguinte = 13–17/07 (seg–sex). `_make_state(**kwargs)` (linha ~15) e `CONFIG` já existem no arquivo de teste.

---

## Task 1: Helper `_search_week`

**Files:**
- Modify: `app/graph/tools.py` (adicionar função nova logo após `_search_any_day`, ~linha 564)
- Test: `tests/test_tools.py` (adicionar no fim do bloco "qualquer dia", após `test_get_available_slots_qualquer_dia_extends_to_next_week_when_few`)

- [ ] **Step 1: Write the failing test**

Adicione em `tests/test_tools.py`:

```python
# ── _search_week — varredura de uma semana específica ─────────────────────────

async def test_search_week_next_week_lists_all_days_with_slots():
    """_search_week(1) sob _FrozenDTTuesday deve varrer 13–17/07 (seg–sex da
    semana seguinte) e listar todos os dias com vaga, sem teto de 3 dias."""
    from app.graph.tools import _search_week

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        # manhã disponível seg/qua/sex da semana seguinte
        if preferred_shift == "manha" and preferred_day in ("2026-07-13", "2026-07-15", "2026-07-17"):
            d = int(preferred_day[-2:])
            return [(datetime(2026, 7, d, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await _search_week(
            week_offset=1,
            calendar_id="cal123",
            doctor="julio",
            preferred_shift="manha",
            slot_duration_minutes=60,
        )

    assert "13/07" in result
    assert "15/07" in result
    assert "17/07" in result
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert "2026-07-07" not in called_days   # nunca tocou a semana atual
    assert "2026-07-20" not in called_days   # nunca passou da semana seguinte


async def test_search_week_this_week_only_remaining_business_days():
    """_search_week(0) sob _FrozenDTTuesday varre só ter–sex (07–10/07),
    nunca segunda (06/07, já passou)."""
    from app.graph.tools import _search_week

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-08":
            return [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await _search_week(
            week_offset=0,
            calendar_id="cal123",
            doctor="julio",
            preferred_shift="manha",
            slot_duration_minutes=60,
        )

    assert "08/07" in result
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert "2026-07-06" not in called_days   # segunda já passou


async def test_search_week_falls_back_to_any_day_when_target_week_empty():
    """Semana alvo vazia → delega a _search_any_day (nunca 'não encontrei')."""
    from app.graph.tools import _search_week

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        # nada na semana seguinte (13–17), só na semana atual (08/07)
        if preferred_shift == "manha" and preferred_day == "2026-07-08":
            return [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await _search_week(
            week_offset=1,
            calendar_id="cal123",
            doctor="julio",
            preferred_shift="manha",
            slot_duration_minutes=60,
        )

    assert "08/07" in result   # veio do fallback _search_any_day
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k "search_week" -v`
Expected: FAIL — `ImportError: cannot import name '_search_week'`.

- [ ] **Step 3: Implement `_search_week`**

Em `app/graph/tools.py`, logo após o fim de `_search_any_day` (antes do `@tool async def get_available_slots`), adicione:

```python
async def _search_week(
    week_offset: int, calendar_id: str, doctor: str,
    preferred_shift: str, slot_duration_minutes: int,
) -> str:
    """Lista os horários de UMA semana específica (offset em relação à atual):
    week_offset=0 → dias úteis restantes desta semana; week_offset>=1 → seg–sex
    daquela semana. Diferente de _search_any_day, não há teto de dias — a semana
    já é um intervalo limitado. Se a semana alvo não tiver nenhuma vaga, delega a
    _search_any_day para nunca terminar com 'não encontrei nada'."""
    from app.google_calendar import get_available_slots as _get_slots

    start, end = _week_range(week_offset)
    sb_busy = await _prefetch_supabase_busy(doctor, start, end)

    found: list[tuple[date, dict]] = []
    for day in _business_days(start, end):
        day_shifts = await _slots_for_any_day(
            day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots,
            supabase_busy=sb_busy,
        )
        if day_shifts:
            found.append((day, day_shifts))

    if not found:
        return await _search_any_day(
            calendar_id=calendar_id,
            doctor=doctor,
            preferred_shift=preferred_shift,
            slot_duration_minutes=slot_duration_minutes,
        )

    sections = [_format_any_day_section(day, day_shifts, preferred_shift) for day, day_shifts in found]
    return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -k "search_week" -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(slots): _search_week lista horários de uma semana específica"
```

---

## Task 2: Roteamento das expressões de semana em `_get_available_slots_impl`

**Files:**
- Modify: `app/graph/tools.py` — inserir bloco novo antes de `if preferred_shift == "qualquer":` (~linha 706) e remover o branch `_vague_patterns` (~linhas 757-762)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests (novo comportamento) e reescrever o teste conflitante**

Adicione ao `tests/test_tools.py`:

```python
# ── get_available_slots — expressão de semana → relação (não pergunta o dia) ──

async def test_get_available_slots_proxima_semana_lists_next_week():
    """'próxima semana' → lista dias da semana seguinte, sem CLARIFICAÇÃO."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-13", "2026-07-15"):
            d = int(preferred_day[-2:])
            return [(datetime(2026, 7, d, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="próxima semana",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "13/07" in result
    assert "15/07" in result


async def test_get_available_slots_proxima_semana_works_with_qualquer_shift():
    """Regressão da armadilha: 'próxima semana' + shift 'qualquer' NÃO pode cair
    em 'Não entendi a data' — o roteamento vem antes do branch de shift."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_day == "2026-07-13" and preferred_shift == "tarde":
            return [(datetime(2026, 7, 13, 14, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="próxima semana",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "Não entendi a data" not in result
    assert "13/07" in result


async def test_get_available_slots_essa_semana_lists_remaining_days():
    """'essa semana' → dias úteis restantes desta semana (ter–sex)."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-09":
            return [(datetime(2026, 7, 9, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="essa semana",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "09/07" in result


async def test_get_available_slots_em_breve_uses_any_day():
    """'em breve' (vago genérico) → próximos dias com vaga, sem CLARIFICAÇÃO."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-07", "2026-07-08"):
            d = int(preferred_day[-2:])
            return [(datetime(2026, 7, d, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="em breve",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "07/07" in result
```

Reescreva o teste conflitante existente `test_get_available_slots_semana_que_vem_still_asks_clarification` (linha ~788) — substitua o corpo inteiro por:

```python
async def test_get_available_slots_semana_que_vem_lists_next_week():
    """'semana que vem' agora lista a semana seguinte em vez de pedir clarificação
    (comportamento novo — antes devolvia CLARIFICAÇÃO NECESSÁRIA)."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-14":
            return [(datetime(2026, 7, 14, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="semana que vem",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "14/07" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k "proxima_semana or essa_semana or em_breve or semana_que_vem" -v`
Expected: novos casos FAIL (ainda cai em CLARIFICAÇÃO / "Não entendi a data"); o reescrito também FAIL.

- [ ] **Step 3: Inserir o roteamento e remover o branch morto**

Em `app/graph/tools.py`, **imediatamente antes** de `# ── "qualquer" shift: check all shifts and return summary ─` / `if preferred_shift == "qualquer":` (~linha 705), insira:

```python
    # ── Expressão de semana (sem dia específico) → oferece a relação da semana
    # em vez de perguntar o dia. Precisa vir ANTES do branch preferred_shift ==
    # "qualquer": para "próxima semana" + shift "qualquer", _parse_day devolve
    # None e aquele branch responderia "Não entendi a data". ──────────────────
    if weekday_key is None:
        _next_week_markers = ("xima semana", "semana que vem", "semana seguinte")
        _this_week_markers = ("essa semana", "esta semana", "dessa semana", "desta semana")
        if any(m in preferred_day_norm for m in _next_week_markers):
            return await _search_week(
                week_offset=1, calendar_id=calendar_id, doctor=doctor,
                preferred_shift=preferred_shift, slot_duration_minutes=slot_duration_minutes,
            )
        if any(m in preferred_day_norm for m in _this_week_markers):
            return await _search_week(
                week_offset=0, calendar_id=calendar_id, doctor=doctor,
                preferred_shift=preferred_shift, slot_duration_minutes=slot_duration_minutes,
            )
        if "em breve" in preferred_day_norm or "semana" in preferred_day_norm:
            return await _search_any_day(
                calendar_id=calendar_id, doctor=doctor,
                preferred_shift=preferred_shift, slot_duration_minutes=slot_duration_minutes,
            )
```

E **remova** o branch antigo agora morto (o bloco de comentário `# ── Vague expressions …` + `_vague_patterns = ("semana", "em breve")` + o `if weekday_key is None and any(...)` que devolve `"CLARIFICAÇÃO NECESSÁRIA: O paciente disse uma expressão vaga…"`, ~linhas 756-762).

> Nota: `"xima semana"` casa tanto "próxima semana" quanto "proxima semana" (sem acento), já que `preferred_day_norm` só faz `.lower().strip()`.

- [ ] **Step 4: Run the new/rewritten tests**

Run: `uv run pytest tests/test_tools.py -k "proxima_semana or essa_semana or em_breve or semana_que_vem" -v`
Expected: todos PASS.

- [ ] **Step 5: Run the full tool + calendar suites (nada regrediu)**

Run: `uv run pytest tests/test_tools.py tests/test_calendar.py --tb=short`
Expected: todos PASS. Em especial, `..._esse_mes` (CLARIFICAÇÃO para mês não reconhecido) e os de `_parse_day("quarta da próxima semana")` seguem verdes.

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(slots): expressão de semana oferece relação em vez de perguntar o dia"
```

---

## Task 3: Ajustar o prompt para não perguntar o dia

**Files:**
- Modify: `app/graph/prompts.py` — linhas ~1221 e ~1429 (texto duplicado em dois blocos de fluxo)

- [ ] **Step 1: Localizar as duas ocorrências**

Run: `grep -n 'consulte DIAS DE ATENDIMENTO / HORÁRIOS DE ATENDIMENTO acima e pergunte qual' app/graph/prompts.py`
Expected: duas linhas (~1221 e ~1429), texto idêntico.

- [ ] **Step 2: Reescrever ambas as ocorrências**

Substitua, em **cada** uma das duas linhas, o texto atual:

```
- Se o paciente disser "próxima semana", "semana que vem", "semana seguinte" ou expressão vaga similar sem especificar um dia, consulte DIAS DE ATENDIMENTO / HORÁRIOS DE ATENDIMENTO acima e pergunte qual dia prefere, oferecendo APENAS os dias listados para o médico deste paciente, ANTES de chamar get_available_slots.
```

por:

```
- Se o paciente disser "próxima semana", "semana que vem", "semana seguinte", "essa semana" ou expressão de semana sem especificar um dia, chame get_available_slots passando essa expressão em preferred_day (ex: preferred_day="próxima semana") e apresente a relação de horários que a ferramenta retornar — NÃO pergunte qual dia ele prefere. Inclua o aviso de que os horários não ficam garantidos até o agendamento ser efetivado.
```

Use `replace_all` OU edite as duas ocorrências individualmente (o texto é idêntico nas duas). As linhas seguintes de cada bloco — a convenção "próxima semana = seg–sex seguinte" (~1222/1430) e a regra "próxima quarta = ocorrência seguinte" (~1223/1431) — **permanecem intactas**.

- [ ] **Step 3: Verificar que as duas foram trocadas e nada mais quebrou**

Run: `grep -n 'pergunte qual dia prefere, oferecendo APENAS' app/graph/prompts.py`
Expected: nenhuma linha (as duas foram substituídas).

Run: `uv run pytest --tb=short`
Expected: suíte inteira PASS (o prompt não tem teste unitário direto de conteúdo; rodar tudo garante que import/format do módulo de prompt segue ok).

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "feat(prompt): Eva oferece relação da semana em vez de perguntar o dia"
```

---

## Verificação final

- [ ] Rodar a suíte inteira: `uv run pytest --tb=short` — tudo verde.
- [ ] Conferir manualmente o script de disponibilidade não foi afetado (ele usa `get_available_slots` de `app/google_calendar.py`, não a tool): `uv run python .claude/skills/doctor-availability-format/fetch_slots.py --doctor bruna --dates 24/08` ainda funciona.

## Fora de escopo (não implementar)

- Proatividade "sempre mostrar a lista no início do agendamento".
- Mudanças no fluxo de dia específico ("quero segunda").
- Alterar `_search_any_day` ou os limites `_ANY_DAY_*`.
