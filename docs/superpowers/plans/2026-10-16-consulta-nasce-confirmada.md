# Consulta nasce confirmada na véspera/mesmo dia — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uma consulta marcada (ou remarcada) quando a data está a 1 dia ou menos (véspera ou mesmo dia) nasce confirmada — grava `confirmed_at`, pinta o evento de verde com ✅ no Google Calendar, registra `appointment_confirmed` — e deixa de receber o lembrete de véspera.

**Architecture:** Um predicado puro `is_born_confirmed(start, now)` em `app/dates.py` decide o caso. Um helper assíncrono compartilhado `_apply_confirmation(...)` em `app/graph/tools.py` aplica os efeitos de calendário + log (reaproveitado por `confirm_attendance`). Cada caminho que escreve `start_time` (Eva, painel, remarcação, reversão, reativação) grava `confirmed_at` no próprio insert/update e chama o helper. A query da véspera em `scripts/send_appointment_reminders.py` passa a ignorar `confirmed_at IS NOT NULL`.

**Tech Stack:** Python 3, FastAPI, LangGraph, Supabase (cliente async), Google Calendar API, pytest + unittest.mock.

**Fuso:** tudo em `America/Recife` (`TZ`). Datas vindas do banco são `timestamptz` e sempre lidas com `.astimezone(TZ)` antes de comparar.

**Rodar os testes:** `uv run pytest --tb=short` (de dentro da worktree). Para um teste só: `uv run pytest tests/test_x.py::test_y -v`.

---

## Task 1: Predicado puro `is_born_confirmed`

**Files:**
- Modify: `app/dates.py` (adicionar função no fim do arquivo)
- Test: `tests/test_dates.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/test_dates.py`. Confirmar que o topo do arquivo já importa `datetime` e define `TZ` (já importa — usa `datetime(..., tzinfo=TZ)` em testes existentes). Se o import de `is_born_confirmed` não existir no bloco de import do topo, adicioná-lo lá junto com os outros imports de `app.dates`.

```python
def test_is_born_confirmed_boundaries():
    from app.dates import is_born_confirmed
    now = datetime(2026, 6, 24, 15, 0, tzinfo=TZ)
    # mesmo dia (mais cedo no mesmo dia também conta)
    assert is_born_confirmed(datetime(2026, 6, 24, 9, 0, tzinfo=TZ), now) is True
    # véspera
    assert is_born_confirmed(datetime(2026, 6, 25, 9, 0, tzinfo=TZ), now) is True
    # dois dias à frente → não
    assert is_born_confirmed(datetime(2026, 6, 26, 9, 0, tzinfo=TZ), now) is False
    # data no passado → não (não é véspera nem mesmo dia)
    assert is_born_confirmed(datetime(2026, 6, 23, 9, 0, tzinfo=TZ), now) is False


def test_is_born_confirmed_uses_recife_date_from_utc():
    from app.dates import is_born_confirmed
    now = datetime(2026, 6, 24, 15, 0, tzinfo=TZ)
    # 2026-06-25T01:00:00+00:00 == 2026-06-24 22:00 em Recife (UTC-3) → mesmo dia
    assert is_born_confirmed(datetime.fromisoformat("2026-06-25T01:00:00+00:00"), now) is True
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `uv run pytest tests/test_dates.py::test_is_born_confirmed_boundaries tests/test_dates.py::test_is_born_confirmed_uses_recife_date_from_utc -v`
Expected: FAIL com `ImportError: cannot import name 'is_born_confirmed'`.

- [ ] **Step 3: Implementar a função**

Adicionar ao fim de `app/dates.py` (o módulo já define `TZ`):

```python
def is_born_confirmed(start: datetime, now: datetime) -> bool:
    """True quando a consulta está a 1 dia ou menos — véspera ou mesmo dia —
    no momento em que ganha o horário. Datas no passado retornam False (não são
    véspera nem mesmo dia). Ambos os argumentos devem ser timezone-aware; a
    comparação é feita pela data real de Recife."""
    days_ahead = (start.astimezone(TZ).date() - now.astimezone(TZ).date()).days
    return 0 <= days_ahead <= 1
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `uv run pytest tests/test_dates.py -v`
Expected: PASS (todos, inclusive os antigos).

- [ ] **Step 5: Commit**

```bash
git add app/dates.py tests/test_dates.py
git commit -m "feat(dates): is_born_confirmed — consulta a 1 dia ou menos"
```

---

## Task 2: Helper compartilhado `_apply_confirmation` + refatorar `confirm_attendance`

Extrai os efeitos de confirmação (pintar o Calendar + logar `appointment_confirmed`) num helper reaproveitável, sem mudar o comportamento de `confirm_attendance`. O helper recebe o `calendar_id` já resolvido; cada caller resolve o seu.

**Files:**
- Modify: `app/graph/tools.py` (adicionar helper; refatorar a cauda de `confirm_attendance`, hoje em `~app/graph/tools.py:3537-3561`)

- [ ] **Step 1: Adicionar o helper `_apply_confirmation`**

Inserir como função de módulo, logo antes da definição da tool `confirm_attendance` (procure por `async def confirm_attendance(` — fica por volta da linha 3432):

```python
async def _apply_confirmation(
    client,
    calendar_id: str | None,
    event_id: str,
    phone: str,
    *,
    auto: bool = True,
) -> None:
    """Aplica os efeitos de confirmação de presença: pinta o evento de verde com
    "✅" no Google Calendar e registra o evento appointment_confirmed. O
    confirmed_at já foi gravado na linha pelo caller (no insert/update). Uma falha
    no Calendar NUNCA derruba o fluxo — só registra um aviso. auto=True marca
    metadata.auto para a auditoria distinguir a confirmação automática
    (véspera/mesmo dia na marcação) da confirmação feita pelo paciente em
    confirm_attendance (auto=False)."""
    if calendar_id:
        try:
            from app.google_calendar import mark_event_confirmed
            await mark_event_confirmed(calendar_id, event_id)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "MARK_CONFIRMED_FAILED appt=%s error=%s", event_id, e
            )
    meta = {"appointment_id": event_id}
    if auto:
        meta["auto"] = True
    await log_event("appointment_confirmed", phone, meta)
```

- [ ] **Step 2: Refatorar a cauda de `confirm_attendance` para usar o helper**

Localizar o bloco atual (a partir do comentário "Marca o evento no Google Calendar como confirmado"), hoje assim:

```python
    # Marca o evento no Google Calendar como confirmado (verde + "✅" no título),
    # para a clínica ver de relance quais consultas o paciente já confirmou. Uma
    # falha aqui não pode derrubar a confirmação em si — só registra no log.
    doctor_id = rows[0].get("doctor_id")
    if doctor_id:
        try:
            cal = (
                await client.from_("doctors")
                .select("agenda_id")
                .eq("doctor_id", doctor_id)
                .single()
                .execute()
            )
            calendar_id = (cal.data or {}).get("agenda_id")
            if calendar_id:
                from app.google_calendar import mark_event_confirmed
                await mark_event_confirmed(calendar_id, appointment_id)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "MARK_CONFIRMED_FAILED appt=%s error=%s", appointment_id, e
            )

    await log_event("appointment_confirmed", config["configurable"]["phone"], {
        "appointment_id": appointment_id,
    })
```

Substituir por (resolve o calendar_id e delega ao helper; `auto=False` porque foi o paciente que confirmou — mantém o metadata exatamente `{"appointment_id": ...}`):

```python
    # Marca o evento no Google Calendar como confirmado (verde + "✅") e registra
    # o evento appointment_confirmed. auto=False: foi o paciente que confirmou,
    # não a regra automática de véspera/mesmo dia na marcação.
    calendar_id = None
    doctor_id = rows[0].get("doctor_id")
    if doctor_id:
        try:
            cal = (
                await client.from_("doctors")
                .select("agenda_id")
                .eq("doctor_id", doctor_id)
                .single()
                .execute()
            )
            calendar_id = (cal.data or {}).get("agenda_id")
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "MARK_CONFIRMED_LOOKUP_FAILED appt=%s error=%s", appointment_id, e
            )

    await _apply_confirmation(
        client, calendar_id, appointment_id,
        config["configurable"]["phone"], auto=False,
    )
```

- [ ] **Step 3: Rodar os testes de `confirm_attendance` (nada deve quebrar)**

Run: `uv run pytest tests/test_tools.py -k confirm_attendance -v`
Expected: PASS. O comportamento é idêntico — mesmo metadata `{"appointment_id": ...}`, mesma pintura do Calendar, mesma tolerância a falha.

- [ ] **Step 4: Commit**

```bash
git add app/graph/tools.py
git commit -m "refactor(tools): extrai _apply_confirmation compartilhado de confirm_attendance"
```

---

## Task 3: Marcação pela Eva — `confirm_appointment` nasce confirmada

**Files:**
- Modify: `app/graph/tools.py` — import no topo + `confirm_appointment` (insert em `~app/graph/tools.py:1578`, após o log `appointment_booked` em `~1600`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim da seção `# ── confirm_appointment ──` em `tests/test_tools.py` (o topo do arquivo já tem `datetime`, `timedelta`, `TZ`, `AsyncMock`, `patch`, `_make_state`, `_make_supabase_client`, `CONFIG`):

```python
async def test_confirm_appointment_born_confirmed_when_tomorrow():
    """Consulta marcada para amanhã nasce confirmada: grava confirmed_at, pinta o
    evento de verde e registra appointment_confirmed(auto)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-tom"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1", "_contact_id": "c-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1", "_contact_id": "c-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime=tomorrow.strftime("%Y-%m-%dT%H:%M:%S"),
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    payload = table.insert.call_args[0][0]
    assert payload.get("confirmed_at") is not None
    mock_mark.assert_awaited_once_with("cal123", "evt-tom")
    assert any(c.args[0] == "appointment_confirmed" for c in mock_log.await_args_list)


async def test_confirm_appointment_not_confirmed_when_far():
    """Consulta a 5 dias segue o fluxo normal: confirmed_at nulo, sem pintar."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    far = (datetime.now(TZ) + timedelta(days=5)).replace(hour=9, minute=0, second=0, microsecond=0)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-far"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1", "_contact_id": "c-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1", "_contact_id": "c-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime=far.strftime("%Y-%m-%dT%H:%M:%S"),
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    payload = table.insert.call_args[0][0]
    assert payload.get("confirmed_at") is None
    mock_mark.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py -k "confirm_appointment_born_confirmed or confirm_appointment_not_confirmed" -v`
Expected: FAIL — `confirmed_at` ausente no payload (hoje o insert não grava a coluna) e `mark_event_confirmed` não é chamado.

- [ ] **Step 3: Garantir o import de `is_born_confirmed` no topo de `app/graph/tools.py`**

Logo após `from app.database import ...` (linha ~15), adicionar:

```python
from app.dates import is_born_confirmed
```

- [ ] **Step 4: Gravar `confirmed_at` no insert e chamar o helper**

Em `confirm_appointment`, localizar o insert (hoje em `~app/graph/tools.py:1578`):

```python
        await client.from_("appointments").insert({
            "patient_id": user["id"] if user else None,
            "contact_id": user.get("_contact_id") if user else None,
            "doctor_id": DOCTOR_IDS.get(doctor),
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": effective_modality or None,
            "consultation_type": consultation_type,
            "booking_fee_waived": _bfw,
            "booking_fee_paid_at": _bfp_at,
        }).execute()
```

Substituir por (calcula o `now`/`born` e grava `confirmed_at`):

```python
        _now = datetime.now(TZ)
        _born_confirmed = is_born_confirmed(start, _now)

        await client.from_("appointments").insert({
            "patient_id": user["id"] if user else None,
            "contact_id": user.get("_contact_id") if user else None,
            "doctor_id": DOCTOR_IDS.get(doctor),
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": effective_modality or None,
            "consultation_type": consultation_type,
            "booking_fee_waived": _bfw,
            "booking_fee_paid_at": _bfp_at,
            # Véspera ou mesmo dia → o paciente acabou de marcar, a presença já
            # está, na prática, confirmada. Nasce confirmada e pula o lembrete.
            "confirmed_at": _now.isoformat() if _born_confirmed else None,
        }).execute()
```

Depois, localizar o log `appointment_booked` (hoje em `~app/graph/tools.py:1600`):

```python
    await log_event("appointment_booked", phone, {
        "doctor": state.get("preferred_doctor"),
        "datetime": slot_datetime,
        "duration_minutes": slot_duration_minutes,
        "patient_name": patient_name,
        "session_note": session_note,
    })
```

Inserir logo DEPOIS desse bloco:

```python
    if _born_confirmed:
        await _apply_confirmation(client, calendar_id, event_id, phone, auto=True)
```

(`calendar_id`, `event_id` e `phone` já estão em escopo nesse ponto.)

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k "confirm_appointment" -v`
Expected: PASS (os dois novos e os antigos de `confirm_appointment`).

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(agendamento): consulta marcada pela Eva na véspera/mesmo dia nasce confirmada"
```

---

## Task 4: Remarcação — `reschedule_appointment` nasce confirmada

Só vale quando o paciente confirma (`confirmed_by_patient`) E a data realmente mudou (troca só de modalidade não conta). Preserva o comportamento atual de zerar `confirmed_at` quando a nova data está longe.

**Files:**
- Modify: `app/graph/tools.py` — `reschedule_appointment` (`reschedule_update` em `~app/graph/tools.py:2757`, update em `~2783`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim da seção `# ── reschedule_appointment ──` em `tests/test_tools.py`:

```python
async def test_reschedule_born_confirmed_when_tomorrow():
    """Remarcar para amanhã nasce confirmada: grava confirmed_at e pinta o evento."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-01-01T09:00:00+00:00",  # data antiga (passado): a data mudou
        "patient_id": "user-1", "patients": {"name": "Maria"},
        "booking_fee_paid_at": None, "booking_fee_waived": False,
        "reschedule_initiated_by": "patient",
    })
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime=tomorrow.strftime("%Y-%m-%dT%H:%M:%S"),
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    payload = table.update.call_args[0][0]
    assert payload.get("confirmed_at") is not None
    mock_mark.assert_awaited_once_with("cal123", "evt-abc")


async def test_reschedule_clears_confirmed_when_far():
    """Remarcar para data distante zera confirmed_at (comportamento atual preservado)."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-01-01T09:00:00+00:00",
        "patient_id": "user-1", "patients": {"name": "Maria"},
        "booking_fee_paid_at": None, "booking_fee_waived": False,
        "reschedule_initiated_by": "patient",
    })
    far = (datetime.now(TZ) + timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime=far.strftime("%Y-%m-%dT%H:%M:%S"),
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    payload = table.update.call_args[0][0]
    assert payload.get("confirmed_at") is None
    mock_mark.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py -k "reschedule_born_confirmed or reschedule_clears_confirmed" -v`
Expected: FAIL no teste "born" (hoje `confirmed_at` é sempre `None` e `mark_event_confirmed` não é chamado). O teste "far" já deve passar (comportamento atual), mas fica como regressão.

- [ ] **Step 3: Calcular o born e gravar `confirmed_at` condicionalmente**

Localizar a construção de `reschedule_update` (hoje em `~app/graph/tools.py:2757`):

```python
    # Update DB record
    new_end = new_start + timedelta(minutes=slot_duration_minutes)
    reschedule_update: dict = {
        "start_time": new_start.isoformat(),
        "end_time": new_end.isoformat(),
        "updated_at": datetime.now(TZ).isoformat(),
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        # A confirmação de presença vale para uma DATA específica. Ao mudar a data,
        # a confirmação da data antiga deixa de valer — o paciente ainda não
        # confirmou a nova. Se confirmed_at sobrevivesse, a trava de idempotência
        # em confirm_attendance suprimiria a confirmação genuína da nova data (nem
        # regravava nem pintava o evento de verde no Calendar). Caso Isaac 13/09.
        "confirmed_at": None,
    }
```

Substituir por:

```python
    # Update DB record
    new_end = new_start + timedelta(minutes=slot_duration_minutes)
    # A nova data nasce confirmada quando o paciente confirma a remarcação E a
    # data realmente mudou E ela cai na véspera/mesmo dia. Caso contrário,
    # confirmed_at é zerado: a confirmação da data antiga não vale para a nova
    # (caso Isaac 13/09) e, longe da data, o lembrete de véspera pede a confirmação.
    _now = datetime.now(TZ)
    _date_changed = old_dt is None or old_dt != new_start
    _born_confirmed = confirmed_by_patient and _date_changed and is_born_confirmed(new_start, _now)
    reschedule_update: dict = {
        "start_time": new_start.isoformat(),
        "end_time": new_end.isoformat(),
        "updated_at": _now.isoformat(),
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        "confirmed_at": _now.isoformat() if _born_confirmed else None,
    }
```

- [ ] **Step 4: Chamar o helper depois do update**

Localizar o update (hoje em `~app/graph/tools.py:2783`):

```python
    await client.from_("appointments").update(reschedule_update).eq("appointment_id", appointment_id).execute()
```

Inserir logo DEPOIS dessa linha:

```python
    if _born_confirmed:
        await _apply_confirmation(client, calendar_id, appointment_id, phone, auto=True)
```

(Nesse ponto `appointment_id` já é o id de evento vigente — o novo, no caso pending; o mesmo, no caso de move. `calendar_id` e `phone` estão em escopo.)

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k "reschedule" -v`
Expected: PASS (novos e antigos de reschedule).

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(remarcação): nova data na véspera/mesmo dia nasce confirmada"
```

---

## Task 5: Reversão — `keep_original_appointment` nasce confirmada

Ao reverter uma remarcação ("deixa como estava"), se o horário original cair na véspera/mesmo dia, nasce confirmada.

**Files:**
- Modify: `app/graph/tools.py` — `keep_original_appointment` (update em `~app/graph/tools.py:2322`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `tests/test_tools.py` (perto dos outros de `keep_original`, ou ao fim do arquivo):

```python
async def test_keep_original_born_confirmed_when_tomorrow():
    """Reverter para o horário original que cai amanhã nasce confirmada."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    end = tomorrow + timedelta(minutes=60)
    execute.return_value = MagicMock(data={
        "appointment_id": "evt-old", "status": "pending_reschedule",
        "patient_id": "user-1",
        "start_time": tomorrow.isoformat(), "end_time": end.isoformat(),
        "modality": "presencial", "patients": {"name": "Maria", "email": "m@x.com"},
    })
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[]), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-new"), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark:
        await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    payload = table.update.call_args[0][0]
    assert payload.get("confirmed_at") is not None
    mock_mark.assert_awaited_once_with("cal123", "evt-new")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py::test_keep_original_born_confirmed_when_tomorrow -v`
Expected: FAIL — `confirmed_at` ausente no update e `mark_event_confirmed` não chamado.
(Se o teste falhar por uma sequência de mocks diferente — p.ex. `.maybe_single()` — ajuste o `execute.return_value`/`side_effect` para casar com as leituras reais da função; o objetivo das asserts é o `confirmed_at` e o `mark_event_confirmed`.)

- [ ] **Step 3: Gravar `confirmed_at` no update e chamar o helper**

Localizar o update em `keep_original_appointment` (hoje em `~app/graph/tools.py:2322`):

```python
    # Só status/evento/flags de remarcação mudam — booking_fee_paid_at/paid_at ficam intactos.
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "reschedule_requested_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()
```

Substituir por:

```python
    # Só status/evento/flags de remarcação mudam — booking_fee_paid_at/paid_at ficam intactos.
    _now = datetime.now(TZ)
    _born_confirmed = is_born_confirmed(start, _now)
    _keep_update = {
        "appointment_id": new_event_id,
        "status": "scheduled",
        "reschedule_requested_at": None,
        "updated_at": _now.isoformat(),
    }
    if _born_confirmed:
        # Horário original na véspera/mesmo dia: nasce confirmada ao ser revertida.
        _keep_update["confirmed_at"] = _now.isoformat()
    await client.from_("appointments").update(_keep_update).eq("appointment_id", appointment_id).execute()

    if _born_confirmed:
        await _apply_confirmation(client, calendar_id, new_event_id, phone, auto=True)
```

(`start`, `calendar_id`, `new_event_id` e `phone` já estão em escopo.)

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k "keep_original" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(reversão): keep_original na véspera/mesmo dia nasce confirmada"
```

---

## Task 6: Reativação dentro de `register_payment` nasce confirmada

No branch que recria o evento de uma consulta cancelada (slot ainda livre), se a data recriada cair na véspera/mesmo dia, nasce confirmada.

**Files:**
- Modify: `app/graph/tools.py` — branch `slot_available and calendar_id` de `register_payment` (update em `~app/graph/tools.py:4449`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

Modelar no teste existente `test_register_payment_reactivation_slot_taken_returns_marker` (em `tests/test_tools.py`, ~linha 5275), mas no branch de slot LIVRE e com a data a 1 dia. Adicionar:

```python
async def test_register_payment_reactivation_born_confirmed_when_tomorrow():
    """Reativar uma consulta cancelada cujo horário original cai amanhã e segue
    livre: recria o evento, grava confirmed_at e pinta o Calendar."""
    from app.graph.tools import register_payment

    julio_id = "d5baa58b-a788-4f40-b8c0-512c189150be"
    slot_start = (datetime.now(TZ) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    slot_end = slot_start + timedelta(hours=1)

    results = [
        # 1. patients ilike (patient_name_override) — não usado sem override, mas mantém a ordem
        MagicMock(data=[{"id": "user-123", "name": "Ricardo", "doctor_id": julio_id}]),
        # 2. scheduled → nenhum
        MagicMock(data=[]),
        # 3. canceled futuro com taxa pendente → defere à reativação
        MagicMock(data=[{"appointment_id": "apt-ricardo"}]),
        # 4. canceled_result da reativação
        MagicMock(data=[{
            "appointment_id": "apt-ricardo",
            "start_time": slot_start.isoformat(),
            "end_time": slot_end.isoformat(),
            "doctor_id": julio_id,
            "modality": "presencial",
        }]),
        # 5. doctors.agenda_id (.single())
        MagicMock(data={"agenda_id": "cal-julio"}),
        # 6. update → scheduled + booking_fee_paid_at + confirmed_at
        MagicMock(data=[]),
        # 7. patients.custom_price (maybe_single)
        MagicMock(data={}),
    ]
    execute = AsyncMock(side_effect=results)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single", "ilike",
              "gte", "gt", "lt", "neq", "order", "insert", "update", "upsert", "is_"):
        getattr(table, m).return_value = table
    table.execute = execute

    events_table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "gte", "order"):
        getattr(events_table, m).return_value = events_table
    events_table.execute = AsyncMock(return_value=MagicMock(data=[]))

    client = MagicMock()
    def _from(name):
        return events_table if name == "events" else table
    client.from_.side_effect = _from

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "contact-1"}), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=[{"id": "user-123"}]), \
         patch("app.patients.get_contacts_for_patient", new_callable=AsyncMock, return_value=[{"phone": "5581988912861"}]), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[]), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-react"), \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(preferred_doctor="julio"),
            config=CONFIG,
        )

    update_payloads = [c.args[0] for c in table.update.call_args_list]
    assert any(p.get("confirmed_at") for p in update_payloads), update_payloads
    mock_mark.assert_awaited_once_with("cal-julio", "evt-react")
```

NOTA ao executor: `register_payment` é longa e a ordem exata das leituras pode divergir deste roteiro de mocks conforme o estado atual do código. Se o teste falhar por sequência de `execute` (e não pela ausência de `confirmed_at`), ajuste a lista `results`/os patches para refletir as leituras reais — sem relaxar as duas asserts finais (`confirmed_at` presente e `mark_event_confirmed` chamado com o calendário e o novo evento).

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py::test_register_payment_reactivation_born_confirmed_when_tomorrow -v`
Expected: FAIL — nenhum update grava `confirmed_at` e `mark_event_confirmed` não é chamado.

- [ ] **Step 3: Gravar `confirmed_at` e chamar o helper no branch de slot livre**

Localizar o branch (hoje em `~app/graph/tools.py:4440`):

```python
                if slot_available and calendar_id:
                    # Slot still free — recreate event and reactivate
                    new_event_id = await create_event(
                        calendar_id, slot_start, slot_minutes, patient_name,
                        canceled_doctor_label.replace("Dr. ", "").replace("Dra. ", ""),
                        modality=canceled_appt.get("modality") or "",
                        patient_email=state.get("patient_email") or "",
                        patient_number=patient_phone,
                    )
                    await client.from_("appointments").update({
                        "status": "scheduled",
                        "booking_fee_paid_at": datetime.now(TZ).isoformat(),
                        "appointment_id": new_event_id,
                        "updated_at": datetime.now(TZ).isoformat(),
                    }).eq("appointment_id", canceled_appt["appointment_id"]).execute()
```

Substituir por:

```python
                if slot_available and calendar_id:
                    # Slot still free — recreate event and reactivate
                    new_event_id = await create_event(
                        calendar_id, slot_start, slot_minutes, patient_name,
                        canceled_doctor_label.replace("Dr. ", "").replace("Dra. ", ""),
                        modality=canceled_appt.get("modality") or "",
                        patient_email=state.get("patient_email") or "",
                        patient_number=patient_phone,
                    )
                    _now = datetime.now(TZ)
                    _born_confirmed = is_born_confirmed(slot_start, _now)
                    _react_update = {
                        "status": "scheduled",
                        "booking_fee_paid_at": _now.isoformat(),
                        "appointment_id": new_event_id,
                        "updated_at": _now.isoformat(),
                    }
                    if _born_confirmed:
                        # Horário reativado na véspera/mesmo dia: nasce confirmada.
                        _react_update["confirmed_at"] = _now.isoformat()
                    await client.from_("appointments").update(_react_update).eq("appointment_id", canceled_appt["appointment_id"]).execute()
                    if _born_confirmed:
                        await _apply_confirmation(client, calendar_id, new_event_id, patient_phone, auto=True)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k "register_payment_reactivation" -v`
Expected: PASS (o novo e o `slot_taken` existente).

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(reativação): register_payment recria evento na véspera/mesmo dia já confirmado"
```

---

## Task 7: Marcação pelo painel — `create_appointments` nasce confirmada

**Files:**
- Modify: `app/panel_booking.py` — import no topo + `create_appointments` (rows em `~app/panel_booking.py:273`, loop de log em `~301`)
- Test: `tests/test_panel_booking.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim da seção `# ── create_appointments ──` em `tests/test_panel_booking.py` (o topo já tem `FROZEN_NOW`, `timedelta`, `TZ`, `_sb`, `_req`, `pb`):

```python
@pytest.mark.asyncio
async def test_create_appointments_born_confirmed_when_tomorrow():
    """Consulta marcada pelo painel para amanhã nasce confirmada."""
    client, q = _sb([])
    tomorrow = (FROZEN_NOW + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt1"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.panel_booking.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_tools_log, \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.panel_booking._notify_clinic_async"):
        await pb.create_appointments(_req(parts=[{"start": tomorrow, "minutes": 60}]))
    rows = q.insert.call_args[0][0]
    assert rows[0].get("confirmed_at") is not None
    mock_mark.assert_awaited_once_with("cal", "evt1")
    assert any(c.args[0] == "appointment_confirmed" for c in mock_tools_log.await_args_list)


@pytest.mark.asyncio
async def test_create_appointments_not_confirmed_when_far():
    """Consulta marcada pelo painel para daqui a 8 dias segue o fluxo normal."""
    client, q = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt1"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.panel_booking.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_calendar.mark_event_confirmed", new_callable=AsyncMock) as mock_mark, \
         patch("app.panel_booking._notify_clinic_async"):
        await pb.create_appointments(_req())  # MON_9 = 2026-10-05, 8 dias à frente do FROZEN_NOW
    rows = q.insert.call_args[0][0]
    assert rows[0].get("confirmed_at") is None
    mock_mark.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_panel_booking.py -k "born_confirmed or not_confirmed_when_far" -v`
Expected: FAIL no "born" (sem `confirmed_at`, sem `mark_event_confirmed`). O "far" já deve passar (regressão).

- [ ] **Step 3: Importar `is_born_confirmed` e `_apply_confirmation`**

No topo de `app/panel_booking.py`, junto aos imports de `app.*` (após a linha `from app.database import ...`), adicionar:

```python
from app.dates import is_born_confirmed
```

Dentro de `create_appointments`, na linha de import local existente:

```python
    from app.graph.tools import _get_doctor_calendar_id
```

trocar por:

```python
    from app.graph.tools import _get_doctor_calendar_id, _apply_confirmation
```

- [ ] **Step 4: Gravar `confirmed_at` nas rows e chamar o helper por parte**

Localizar `now_iso` (hoje em `~app/panel_booking.py:251`) e a montagem de `rows` (hoje em `~273`). Logo após `now_iso = ...`, garantir um `now` tz-aware do relógio do módulo:

```python
    now_iso = datetime.now(TZ).isoformat()
    _clock = _now()  # relógio do módulo (testável/congelável); NÃO sombrear a função _now
```

Ajustar a list comprehension de `rows` para gravar `confirmed_at` por parte:

```python
        rows = [
            {
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
                "confirmed_at": _clock.isoformat() if is_born_confirmed(start, _clock) else None,
                **fee,
            }
            for event_id, start, minutes, note in created
        ]
```

Localizar o loop de log (hoje em `~app/panel_booking.py:301`):

```python
    for event_id, start, minutes, note in created:
        await log_event("appointment_booked", req["phone"], {
            "doctor": doctor, "datetime": start.replace(tzinfo=None).isoformat(),
            "duration_minutes": minutes, "patient_name": name, "session_note": note,
            "origem": "painel", "atendente": req.get("agent") or "", "encaixe": bool(req.get("encaixe")),
            "appointment_id": event_id,
        })
```

Adicionar dentro do loop, logo após o `await log_event(...)`:

```python
        if is_born_confirmed(start, _clock):
            await _apply_confirmation(client, calendar_id, event_id, req["phone"], auto=True)
```

(`client` e `calendar_id` já estão em escopo nesse ponto.)

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_panel_booking.py -v`
Expected: PASS (novos e antigos).

- [ ] **Step 6: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "feat(painel): marcação na véspera/mesmo dia nasce confirmada"
```

---

## Task 8: Lembrete de véspera ignora consultas já confirmadas

**Files:**
- Modify: `scripts/send_appointment_reminders.py` — query day-before (`~scripts/send_appointment_reminders.py:218-227`)
- Test: `tests/test_reminders.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/test_reminders.py` (o topo já tem `os`, `MagicMock`, `AsyncMock`, `patch`, `datetime`, `TZ`, `rem`):

```python
@pytest.mark.asyncio
async def test_day_before_query_skips_confirmed():
    """A query da véspera filtra confirmed_at IS NULL: consulta já confirmada —
    inclusive a que nasceu confirmada na marcação — não recebe o lembrete."""
    from datetime import datetime as _dt

    class _FakeDt(_dt):
        @classmethod
        def now(cls, tz=None):
            return _dt(2026, 6, 19, 7, 0, tzinfo=TZ)  # dentro da janela 7h-12h

    table = MagicMock()
    for m in ("select", "eq", "is_", "gt", "gte", "lt", "lte"):
        getattr(table, m).return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_appointment_reminders.datetime", _FakeDt), \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        await rem.main()

    is_filters = [c.args for c in table.is_.call_args_list]
    assert ("confirmed_at", "null") in is_filters, is_filters
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_reminders.py::test_day_before_query_skips_confirmed -v`
Expected: FAIL — `("confirmed_at", "null")` não está entre as chamadas de `.is_`.

- [ ] **Step 3: Adicionar o filtro na query**

Localizar a query day-before (hoje em `~scripts/send_appointment_reminders.py:218`):

```python
        result = await (
            client.from_("appointments")
            .select("appointment_id, start_time, doctor_id, modality, patient_id, contact_id, patients(name)")
            .eq("status", "scheduled")
            .is_("reminder_day_before_sent_at", "null")
            .gte("start_time", f"{tomorrow_start}T00:00:00")
            .lt("start_time", f"{tomorrow_end}T00:00:00")
            .lte("created_at", booked_before)
            .execute()
        )
```

Adicionar a linha `.is_("confirmed_at", "null")` logo após o filtro de `reminder_day_before_sent_at`:

```python
        result = await (
            client.from_("appointments")
            .select("appointment_id, start_time, doctor_id, modality, patient_id, contact_id, patients(name)")
            .eq("status", "scheduled")
            .is_("reminder_day_before_sent_at", "null")
            # Consulta já confirmada (inclusive a que nasceu confirmada na véspera/
            # mesmo dia) não precisa do lembrete de confirmação de presença.
            .is_("confirmed_at", "null")
            .gte("start_time", f"{tomorrow_start}T00:00:00")
            .lt("start_time", f"{tomorrow_end}T00:00:00")
            .lte("created_at", booked_before)
            .execute()
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: PASS (novo e antigos).

- [ ] **Step 5: Commit**

```bash
git add scripts/send_appointment_reminders.py tests/test_reminders.py
git commit -m "feat(lembrete): véspera ignora consulta já confirmada (confirmed_at IS NULL)"
```

---

## Task 9: Suíte completa + fechamento

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS (todos). Se algo quebrar, corrigir antes de seguir. Suspeitos prováveis de quebra por mudança de comportamento: testes antigos de `confirm_appointment` / `reschedule_appointment` / `create_appointments` que usam datas fixas — confirmar que essas datas estão no passado ou a 2+ dias (não nascem confirmadas) e, portanto, seguem inalterados.

- [ ] **Step 2: Conferência rápida de cobertura do spec**

Confirmar que cada caminho do spec virou código + teste: Eva (Task 3), painel (Task 7), remarcação (Task 4), reversão (Task 5), reativação (Task 6), lembrete (Task 8), predicado (Task 1), helper (Task 2).

- [ ] **Step 3: Commit final (se sobrou algo não commitado)**

```bash
git add -A
git commit -m "chore: consulta nasce confirmada na véspera/mesmo dia — fechamento" || echo "nada a commitar"
```

---

## Notas de integração (para quem executa)

- Padrão de mock do Supabase: o cliente é encadeável — cada método (`select`, `eq`, `is_`, `insert`, `update`, ...) retorna a própria `table`, e `execute` é um `AsyncMock`. Inspecione o payload gravado com `table.insert.call_args[0][0]` / `table.update.call_args[0][0]`.
- `mark_event_confirmed` é importado DENTRO de `_apply_confirmation` (`from app.google_calendar import mark_event_confirmed`), então patchar `app.google_calendar.mark_event_confirmed` funciona.
- `_apply_confirmation` usa o `log_event` de `app.graph.tools`. No teste do painel, o `appointment_confirmed` passa por `app.graph.tools.log_event` (não `app.panel_booking.log_event`).
- Datas: sempre tz-aware. O predicado compara pela data real de Recife (`.astimezone(TZ).date()`). Testes novos derivam as datas de `datetime.now(TZ)` (ou `FROZEN_NOW` no painel) para não envelhecerem.
