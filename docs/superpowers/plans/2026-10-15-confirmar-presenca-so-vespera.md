# Confirmação de presença só na véspera — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Eva só grava a confirmação de presença (`confirmed_at`) quando a consulta for hoje ou amanhã; consultas mais distantes nunca recebem marcação de confirmação, e o passivo já gravado é limpo.

**Architecture:** Trava determinística dentro de `confirm_attendance` (lê `start_time` e recusa a gravação se faltar 2+ dias), função simétrica no Google Calendar para desfazer a marca de confirmado, e um script one-off que zera o passivo no banco e no Calendar.

**Tech Stack:** Python, LangGraph tools, Supabase (postgrest), Google Calendar API, pytest.

**Trabalhe dentro da worktree** `.worktrees/confirmar-presenca-so-vespera`. Rode os testes com `uv run pytest --tb=short`.

---

### Task 1: Função que desfaz a marca de confirmado no Calendar

**Files:**
- Modify: `app/google_calendar.py` (adicionar após a linha 1053, logo depois de `mark_event_confirmed`)
- Test: `tests/test_calendar.py` (adicionar após a linha 1029, no bloco de `mark_event_confirmed`)

A marca de confirmado é o prefixo `CONFIRMED_PREFIX = "✅ "` no título mais `CONFIRMED_COLOR_ID = "2"` (verde). Eventos normais nascem sem `colorId`, então "voltar ao padrão" é limpar o `colorId` com `None`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar em `tests/test_calendar.py` após a linha 1029:

```python
async def test_mark_event_unconfirmed_strips_checkmark_and_clears_color():
    from app.google_calendar import mark_event_unconfirmed

    service = MagicMock()
    service.events.return_value.get.return_value.execute.return_value = {
        "summary": "✅ Consulta — Maria [Presencial]",
    }
    patch_mock = service.events.return_value.patch
    patch_mock.return_value.execute.return_value = {}

    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        await mark_event_unconfirmed("cal-test", "evt-1")

    _, kwargs = patch_mock.call_args
    body = kwargs["body"]
    assert body["colorId"] is None                       # volta à cor padrão
    assert "✅" not in body["summary"]
    assert body["summary"] == "Consulta — Maria [Presencial]"


async def test_mark_event_unconfirmed_is_noop_on_unmarked_title():
    """Evento sem marca: título fica igual (não há ✅ para remover); cor volta ao padrão."""
    from app.google_calendar import mark_event_unconfirmed

    service = MagicMock()
    service.events.return_value.get.return_value.execute.return_value = {
        "summary": "Consulta — Maria [Presencial]",
    }
    patch_mock = service.events.return_value.patch
    patch_mock.return_value.execute.return_value = {}

    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        await mark_event_unconfirmed("cal-test", "evt-1")

    _, kwargs = patch_mock.call_args
    body = kwargs["body"]
    assert body["summary"] == "Consulta — Maria [Presencial]"
    assert body["colorId"] is None
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/test_calendar.py -k mark_event_unconfirmed -v`
Expected: FAIL com `ImportError: cannot import name 'mark_event_unconfirmed'`.

- [ ] **Step 3: Implementar a função**

Adicionar em `app/google_calendar.py` após a linha 1053:

```python
def _unmark_event_confirmed(service, calendar_id: str, event_id: str) -> None:
    # Desfaz a marca de confirmado: tira o "✅ " do título (preservando o resto,
    # inclusive edições manuais da clínica) e limpa o colorId para voltar à cor
    # padrão do calendário. Simétrico a _mark_event_confirmed.
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    summary = event.get("summary") or ""
    prefix = CONFIRMED_PREFIX.strip()
    if summary.startswith(prefix):
        summary = summary[len(prefix):].lstrip()
    patch = {"summary": summary, "colorId": None}
    service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch).execute()


async def mark_event_unconfirmed(calendar_id: str, event_id: str) -> None:
    """Desfaz a marca de confirmado: remove o "✅" do título e volta a cor ao padrão."""
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _unmark_event_confirmed, service, calendar_id, event_id)
```

- [ ] **Step 4: Rodar para ver passar**

Run: `uv run pytest tests/test_calendar.py -k mark_event_unconfirmed -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/google_calendar.py tests/test_calendar.py
git commit -m "feat(calendar): mark_event_unconfirmed desfaz a marca de confirmado

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Trava de véspera em `confirm_attendance`

**Files:**
- Modify: `app/graph/tools.py` (SELECT na linha 3314; novo bloco de guarda entre a linha 3374 e a 3376)
- Test: `tests/test_tools.py` (adicionar após a linha 2404, antes do bloco `# ── cancel_appointment`)

A tool hoje grava `confirmed_at` sem olhar a data. Regra: só grava se a consulta for hoje ou amanhã no fuso de Recife (`TZ`). `start_time` vem do banco em UTC; a conversão canônica do arquivo é `datetime.fromisoformat(st).astimezone(TZ)` (ver `app/graph/tools.py:2188-2189`). Dado ausente ou ilegível é fail-open (não bloqueia), para nunca travar uma confirmação legítima por anomalia de dado.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar em `tests/test_tools.py` após a linha 2404:

```python
async def test_confirm_attendance_rejects_when_more_than_one_day_ahead():
    """Regra: a presença só é confirmada com 1 dia de antecedência. Uma consulta a
    vários dias de distância NÃO pode ter confirmed_at gravado (consultas futuras não
    devem ter marcação de confirmação)."""
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    far = (datetime.now(TZ) + timedelta(days=5)).isoformat()
    execute.return_value = MagicMock(data=[{
        "confirmed_at": None, "status": "scheduled",
        "patient_id": "p-1", "start_time": far,
    }])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "p-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-distante", state=_make_state(), config=CONFIG,
        )
    table.update.assert_not_called()         # NÃO gravou confirmed_at
    mock_log.assert_not_awaited()            # NÃO logou
    assert "INSTRUÇÃO INTERNA" in result
    assert not result.startswith("Presença confirmada")


async def test_confirm_attendance_accepts_when_appointment_is_tomorrow():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).isoformat()
    execute.return_value = MagicMock(data=[{
        "confirmed_at": None, "status": "scheduled",
        "patient_id": "p-1", "start_time": tomorrow,
    }])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "p-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-amanha", state=_make_state(), config=CONFIG,
        )
    table.update.assert_called()
    mock_log.assert_awaited()
    assert "confirmada" in result.lower()


async def test_confirm_attendance_accepts_when_appointment_is_today():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    today = datetime.now(TZ).isoformat()
    execute.return_value = MagicMock(data=[{
        "confirmed_at": None, "status": "scheduled",
        "patient_id": "p-1", "start_time": today,
    }])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "p-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-hoje", state=_make_state(), config=CONFIG,
        )
    table.update.assert_called()
    mock_log.assert_awaited()
    assert "confirmada" in result.lower()
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `uv run pytest tests/test_tools.py -k "confirm_attendance_rejects_when_more_than_one_day_ahead or confirm_attendance_accepts_when_appointment" -v`
Expected: FAIL — `test_...rejects_when_more_than_one_day_ahead` falha porque hoje a tool grava mesmo com consulta distante (`table.update` é chamado). Os dois `accepts_*` devem passar desde já (comportamento atual permite), mas ainda não exercem a trava.

- [ ] **Step 3: Incluir `start_time` no SELECT**

Em `app/graph/tools.py`, linha 3314, trocar:

```python
        .select("confirmed_at, status, patient_id, doctor_id")
```

por:

```python
        .select("confirmed_at, status, patient_id, doctor_id, start_time")
```

- [ ] **Step 4: Adicionar a guarda de véspera**

Em `app/graph/tools.py`, inserir ENTRE o fim do bloco de idempotência (logo após a linha 3374, que fecha o `return` do `if rows[0].get("confirmed_at"):`) e o UPDATE da linha 3376:

```python
    # Regra de negócio: a presença só é confirmada com 1 dia de antecedência (a
    # partir da véspera). Consultas mais distantes NÃO podem ter confirmed_at
    # gravado — o lembrete de véspera pedirá a confirmação na hora certa. Sem esta
    # trava, uma mensagem afirmativa do paciente ("sim", "confirmado") numa conversa
    # com uma consulta futura listada marcava presença dias antes.
    # start_time vem do banco em UTC; .astimezone(TZ) dá a data real de Recife.
    start_time_str = rows[0].get("start_time")
    if start_time_str:
        try:
            appt_date = datetime.fromisoformat(start_time_str).astimezone(TZ).date()
            days_ahead = (appt_date - datetime.now(TZ).date()).days
        except (ValueError, TypeError):
            days_ahead = 0  # data ilegível não bloqueia a confirmação (fail-open)
        if days_ahead >= 2:
            return (
                "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Ainda não é a véspera "
                "desta consulta, então NÃO confirme a presença agora e NÃO diga "
                '"presença confirmada". A confirmação de presença só vale a partir '
                "do dia anterior à consulta. Agradeça o paciente de forma acolhedora "
                "e diga que vai pedir a confirmação mais perto da data da consulta."
            )
```

- [ ] **Step 5: Rodar os testes novos e ver passar**

Run: `uv run pytest tests/test_tools.py -k "confirm_attendance" -v`
Expected: PASS — inclusive os 3 novos e todos os antigos de `confirm_attendance` (os antigos mockam sem `start_time`, caindo no fail-open, então seguem passando).

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "fix(confirmacao): só confirma presença com 1 dia de antecedência

confirm_attendance passa a ler start_time e recusa gravar confirmed_at
quando a consulta está a 2+ dias. Consultas futuras não recebem mais
marcação de confirmação antecipada.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Script one-off para limpar o passivo

**Files:**
- Create: `scripts/_limpar_confirmacoes_futuras_oneoff.py`

Zera `confirmed_at` das consultas `scheduled` já confirmadas que estão a 2+ dias de distância e desfaz a marca no Calendar. Dry-run por padrão; aplica só com `--apply`. **Este script não roda neste Mac** (o DNS do Supabase é recusado localmente); ele é entregue para rodar no servidor da Eva.

- [ ] **Step 1: Criar o script**

```python
"""One-off: zera confirmed_at de consultas FUTURAS (2+ dias) que ficaram marcadas
como confirmadas antes da trava de véspera, e desfaz a marca no Google Calendar.

Uso:
    uv run python scripts/_limpar_confirmacoes_futuras_oneoff.py           # dry-run (só lista)
    uv run python scripts/_limpar_confirmacoes_futuras_oneoff.py --apply   # aplica

Rodar no servidor da Eva (o DNS do Supabase é recusado no Mac local).
"""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")


async def main(apply: bool) -> None:
    from app.supabase_client import get_supabase
    from app.google_calendar import mark_event_unconfirmed

    client = await get_supabase()
    res = (
        await client.from_("appointments")
        .select("appointment_id, start_time, confirmed_at, status, doctor_id, patients(name)")
        .eq("status", "scheduled")
        .not_.is_("confirmed_at", "null")
        .order("start_time")
        .execute()
    )
    rows = res.data or []
    today = datetime.now(TZ).date()

    alvos = []
    for r in rows:
        st = r.get("start_time")
        if not st:
            continue
        try:
            appt_date = datetime.fromisoformat(st).astimezone(TZ).date()
        except (ValueError, TypeError):
            continue
        if (appt_date - today).days >= 2:
            alvos.append(r)

    print(f"{len(alvos)} consulta(s) futura(s) confirmada(s) a 2+ dias:")
    for r in alvos:
        nome = (r.get("patients") or {}).get("name", "?")
        print(f"  {r['appointment_id']} | {r['start_time']} | {nome}")

    if not apply:
        print("\nDry-run. Rode com --apply para zerar confirmed_at e desmarcar no Calendar.")
        return

    agendas: dict[str, str] = {}
    for r in alvos:
        appt_id = r["appointment_id"]
        await client.from_("appointments").update(
            {"confirmed_at": None}
        ).eq("appointment_id", appt_id).execute()

        doctor_id = r.get("doctor_id")
        if doctor_id:
            if doctor_id not in agendas:
                d = (
                    await client.from_("doctors")
                    .select("agenda_id")
                    .eq("doctor_id", doctor_id)
                    .single()
                    .execute()
                )
                agendas[doctor_id] = (d.data or {}).get("agenda_id") or ""
            cal = agendas[doctor_id]
            if cal:
                try:
                    await mark_event_unconfirmed(cal, appt_id)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! Calendar falhou em {appt_id}: {e}")
        print(f"  ✓ {appt_id} desconfirmada")

    print(f"\nPronto: {len(alvos)} consulta(s) processada(s).")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
```

- [ ] **Step 2: Conferir que importa sem erro de sintaxe**

Run: `uv run python -c "import ast; ast.parse(open('scripts/_limpar_confirmacoes_futuras_oneoff.py').read()); print('ok')"`
Expected: `ok`. (Não executar o script aqui — ele toca o Supabase, cujo DNS é recusado neste Mac.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_limpar_confirmacoes_futuras_oneoff.py
git commit -m "chore(script): one-off limpa confirmações de consultas futuras

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Suíte completa verde

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: todos passam (sem falhas nem erros). Se algum teste de `confirm_attendance` ou de Calendar quebrar, investigar antes de prosseguir.

- [ ] **Step 2: Se tudo verde, nada a commitar aqui** (os commits já foram feitos por tarefa).

---

## Notas de execução

- A limpeza do passivo (Task 3) só tem efeito quando rodada no servidor com `--apply`; o PR em si entrega o código e a trava. Rode o dry-run primeiro e confira a lista com a clínica antes do `--apply`.
- Não mexer na seleção do lembrete de véspera (`scripts/send_appointment_reminders.py`), no prompt (`app/graph/prompts.py`), nem em `reschedule_appointment` — fora de escopo.
