# Segunda cobrança de confirmação na tarde da véspera — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enviar uma segunda cobrança de confirmação de presença, uma vez por consulta, na tarde da véspera (14h–18h Recife), só para quem recebeu o lembrete da manhã e ainda não confirmou.

**Architecture:** Terceiro bloco no cron existente `scripts/send_appointment_reminders.py` (roda de 10 em 10 min). Reusa `_send_reminder_to_contacts`. Idempotência por coluna nova `confirmation_nudge_sent_at` em `appointments`, gravada só no sucesso. Template novo da Meta `lembrete_confirmacao_tarde`.

**Tech Stack:** Python async, supabase-py, Meta WhatsApp templates via Chatwoot, pytest.

---

## Arquivos

- Modificar: `scripts/send_appointment_reminders.py`
  - `_plain_message()` — nova ramificação para o template `lembrete_confirmacao_tarde`.
  - `main()` — novo bloco de seleção da tarde + entrada no `batch`.
  - Cabeçalho do módulo — documentar a nova coluna e a janela.
- Testar: `tests/test_reminders.py`
- Banco (manual, fora do código): `ALTER TABLE appointments ADD COLUMN ...`

## Decisões fixas

- Nome do template Meta: `lembrete_confirmacao_tarde` (categoria UTILITY, `pt_BR`, 3 variáveis: nome, médico, horário). O código referencia esse nome exato.
- Texto neutro quanto à modalidade (não assume online nem presencial), diferente do lembrete da manhã.
- Janela: `14 <= now.hour < 18`.
- Sem variante online e sem terceira cobrança (YAGNI).

---

### Task 1: Texto da cobrança em `_plain_message`

**Files:**
- Modify: `scripts/send_appointment_reminders.py` (função `_plain_message`)
- Test: `tests/test_reminders.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione ao fim de `tests/test_reminders.py`:

```python
def test_plain_message_confirmation_nudge():
    msg = rem._plain_message(
        "lembrete_confirmacao_tarde", "João", "Dr. Júlio", "14:00")
    assert "João" in msg
    assert "Dr. Júlio" in msg
    assert "14:00" in msg
    assert "confirma" in msg.lower()
    # Neutro quanto à modalidade: sem "online" nem "Psiquê" no texto
    assert "online" not in msg.lower()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `uv run pytest tests/test_reminders.py::test_plain_message_confirmation_nudge -q`
Expected: FAIL (hoje o template desconhecido cai no texto do "dia da consulta", que contém "Hoje é o dia").

- [ ] **Step 3: Implementar a ramificação**

Em `_plain_message`, logo após o bloco `if template_name in ("lembrete_dia_anteior", "lembrete_dia_anterior_online"):` e antes do comentário `# Day-of reminder`, inserir:

```python
    if template_name == "lembrete_confirmacao_tarde":
        return (
            f"Olá! Ainda não recebi a confirmação de presença de {first_name} "
            f"na consulta de amanhã com {doctor_label} às {time_str}. "
            f"Consegue confirmar pra mim?"
        )
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `uv run pytest tests/test_reminders.py::test_plain_message_confirmation_nudge -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/send_appointment_reminders.py tests/test_reminders.py
git commit -m "feat(lembrete): texto da 2ª cobrança de confirmação (tarde)"
```

---

### Task 2: Envio reusa `_send_reminder_to_contacts` com a coluna nova

Confere que a função genérica de envio grava `confirmation_nudge_sent_at` quando chamada com esse `sent_col`. Nenhuma mudança de código esperada — é um teste de contrato para travar o comportamento.

**Files:**
- Test: `tests/test_reminders.py`

- [ ] **Step 1: Escrever o teste que falha (ou já passa)**

Adicione ao fim de `tests/test_reminders.py`:

```python
@pytest.mark.asyncio
async def test_nudge_marks_confirmation_nudge_sent_at():
    client, table = _client()
    now = datetime(2026, 6, 19, 14, 0, tzinfo=TZ)
    with patch("scripts.send_appointment_reminders.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]), \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock) as mock_send:
        sent = await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_confirmacao_tarde",
            "confirmation_nudge_sent_at", now, None)
    assert sent == ["5581111"]
    mock_send.assert_awaited_once()
    # template enviado é o da cobrança (sem troca para variante online)
    assert mock_send.await_args.args[1] == "lembrete_confirmacao_tarde"
    # grava a coluna nova uma vez
    table.update.assert_called_once()
    assert table.update.call_args.args[0] == {"confirmation_nudge_sent_at": now.isoformat()}
```

- [ ] **Step 2: Rodar o teste**

Run: `uv run pytest tests/test_reminders.py::test_nudge_marks_confirmation_nudge_sent_at -q`
Expected: PASS (a função já é genérica no `sent_col`). Se falhar no `args[1]`, confirme que `send_reminder_template` é chamado como `send_reminder_template(phone, effective_template, ...)` e ajuste o índice do argumento no assert conforme a assinatura real, sem mudar o código de produção.

- [ ] **Step 3: Commit**

```bash
git add tests/test_reminders.py
git commit -m "test(lembrete): envio da cobrança grava confirmation_nudge_sent_at"
```

---

### Task 3: Bloco da tarde em `main()` + wiring no batch + doc da coluna

**Files:**
- Modify: `scripts/send_appointment_reminders.py` (`main()` e cabeçalho do módulo)
- Test: `tests/test_reminders.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao fim de `tests/test_reminders.py`:

```python
def _chain_client():
    table = MagicMock()
    for m in ("select", "eq", "is_", "gt", "gte", "lt", "lte"):
        getattr(table, m).return_value = table
    # `.not_` é acessado como atributo (não chamado) e expõe `.is_`. Fazendo
    # table.not_ = table, o `.not_.is_(...)` vira o mesmo table.is_ já encadeado,
    # e suas chamadas entram em table.is_.call_args_list.
    table.not_ = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table
    return client, table


@pytest.mark.asyncio
async def test_nudge_query_runs_inside_afternoon_window():
    client, table = _chain_client()
    fake_now = datetime(2026, 6, 19, 14, 0, tzinfo=TZ)

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now
        fromisoformat = staticmethod(datetime.fromisoformat)

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_appointment_reminders.datetime", _FakeDatetime), \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        await rem.main()

    # not_.is_ e is_ compartilham o mesmo mock (ver _chain_client), então todas
    # as colunas filtradas aparecem em table.is_.call_args_list.
    is_cols = [c.args[0] for c in table.is_.call_args_list]
    assert "confirmed_at" in is_cols
    assert "confirmation_nudge_sent_at" in is_cols
    assert "reminder_day_before_sent_at" in is_cols


@pytest.mark.asyncio
async def test_nudge_query_skipped_outside_window():
    client, table = _chain_client()
    fake_now = datetime(2026, 6, 19, 19, 0, tzinfo=TZ)  # 19h: fora da janela

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now
        fromisoformat = staticmethod(datetime.fromisoformat)

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_appointment_reminders.datetime", _FakeDatetime), \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        await rem.main()

    is_cols = [c.args[0] for c in table.is_.call_args_list]
    assert "confirmation_nudge_sent_at" not in is_cols
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_reminders.py -k nudge_query -q`
Expected: FAIL (o bloco da tarde ainda não existe, então `confirmation_nudge_sent_at` nunca aparece nos filtros).

- [ ] **Step 3: Implementar o bloco de seleção em `main()`**

Em `main()`, logo após o bloco `# ── Day-before reminders` (depois da linha `day_before_appts = result.data or []`) e antes de `# ── Day-of reminders`, inserir:

```python
    # ── Confirmation nudge: tarde da véspera, 14h Recife, janela até 18h ────────
    # Segunda (e última) cobrança só para quem recebeu o lembrete da manhã e ainda
    # não confirmou presença. confirmation_nudge_sent_at só é gravado no sucesso,
    # então rodar de novo depois de um restart não duplica o envio.
    confirmation_nudge_appts = []
    if 14 <= now.hour < 18:
        result = await (
            client.from_("appointments")
            .select("appointment_id, start_time, doctor_id, modality, patient_id, contact_id, patients(name)")
            .eq("status", "scheduled")
            .not_.is_("reminder_day_before_sent_at", "null")
            .is_("confirmed_at", "null")
            .is_("confirmation_nudge_sent_at", "null")
            .gte("start_time", f"{tomorrow_start}T00:00:00")
            .lt("start_time", f"{tomorrow_end}T00:00:00")
            .execute()
        )
        confirmation_nudge_appts = result.data or []
```

- [ ] **Step 4: Ligar no print e no batch**

Após a linha `print(f"Day-of reminders to send: {len(day_of_appts)}")`, inserir:

```python
    print(f"Confirmation nudges to send: {len(confirmation_nudge_appts)}")
```

No dicionário/lista `batch = [ ... ]`, adicionar uma terceira linha:

```python
        batch = [
            *((a, "lembrete_dia_anteior", "reminder_day_before_sent_at") for a in day_before_appts),
            *((a, "lembrete_dia_consulta", "reminder_day_of_sent_at") for a in day_of_appts),
            *((a, "lembrete_confirmacao_tarde", "confirmation_nudge_sent_at") for a in confirmation_nudge_appts),
        ]
```

- [ ] **Step 5: Documentar a coluna no cabeçalho do módulo**

No docstring do topo do arquivo, na seção `Requires in Supabase:`, adicionar a linha:

```
  ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmation_nudge_sent_at timestamptz;
```

E na lista de bullets do topo, adicionar:

```
- Afternoon nudge: sends between 14h-18h Recife the day before, only when the
  morning reminder already went out and the patient has not confirmed yet.
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_reminders.py -k nudge_query -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/send_appointment_reminders.py tests/test_reminders.py
git commit -m "feat(lembrete): 2ª cobrança de confirmação na tarde da véspera"
```

---

### Task 4: Suíte completa + checagem do scheduler

**Files:** nenhum (verificação)

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: todos passam, incluindo os testes antigos de `test_reminders.py` (o comportamento da manhã e do dia da consulta não muda).

- [ ] **Step 2: Conferir que o job já existe no agendador**

Abrir `app/scheduler.py` e confirmar que o job `appointment_reminders` → `send_appointment_reminders` com `Every(10)` já está em `JOBS`. Nenhuma alteração necessária: o bloco novo roda dentro do mesmo job. Nada a commitar.

---

## Passos manuais (fora do código, feitos pela clínica/você)

1. **Banco de produção:** rodar antes do deploy valer:
   ```sql
   ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmation_nudge_sent_at timestamptz;
   ```
2. **Template Meta:** criar `lembrete_confirmacao_tarde` (UTILITY, `pt_BR`, 3 variáveis na ordem nome/médico/horário) e aguardar aprovação. Antes da aprovação o envio falha silencioso, não grava `confirmation_nudge_sent_at` e tenta de novo no ciclo seguinte — nada quebra.

## Fora de escopo

- Terceira cobrança ou toque no dia da consulta.
- Variante online do template.
- Mudanças em `confirm_attendance` ou nos lembretes da manhã e do dia.
