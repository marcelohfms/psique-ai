# Envio híbrido (texto/template) do cron de taxa de reserva — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o lembrete de taxa de reserva e o aviso de cancelamento por não-pagamento serem entregáveis fora da janela de 24h do WhatsApp, usando template aprovado quando a janela está fechada e texto livre quando está aberta — eliminando o cancelamento silencioso.

**Architecture:** Duas funções novas em `scripts/send_payment_reminders.py`: `_window_open` (a conversa teve mensagem do paciente nas últimas 24h?) e `_notify` (roteia entre `send_whatsapp` texto-livre e `send_template_message` via Chatwoot). Os dois fluxos existentes (`_send_payment_reminder`, `_cancel_unpaid_appointment`) passam a chamar `_notify`; o guard `any_notified` do cancelamento continua igual, mas agora com significado real de entrega.

**Tech Stack:** Python 3.14, pytest + unittest.mock (AsyncMock), Supabase (`messages`/`appointments`), Chatwoot (`send_template_message`), Meta WhatsApp templates.

**Spec:** `docs/superpowers/specs/2026-08-14-taxa-reserva-template-fora-janela-design.md`

---

## File Structure

- **Modify:** `scripts/send_payment_reminders.py`
  - novo: `_window_open`, `_notify`, `_send_template`, `_consulta_ref`; constantes de template/janela; `timezone` no import.
  - alterado: bloco de envio em `_send_payment_reminder` e `_cancel_unpaid_appointment`.
- **Modify (tests):** `tests/test_payment_reminders_cancel.py` (arquivo de teste existente deste cron — não criar arquivo novo, conforme CLAUDE.md).

O corpo dos templates (`taxa_reserva_lembrete`, `taxa_reserva_cancelamento`) é cadastrado pela clínica no Meta — fora do escopo de código; nomes/params fixados no código conforme o spec.

---

## Task 1: Constantes e import

**Files:**
- Modify: `scripts/send_payment_reminders.py:23` e `:40` (após os dicionários DOCTOR_*)

- [ ] **Step 1: Adicionar `timezone` ao import de datetime**

Trocar a linha `from datetime import datetime, timedelta` por:

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 2: Adicionar constantes após `DOCTOR_KEYS` (logo depois da linha 40)**

```python
# Templates aprovados no Meta (UTILITY, pt_BR) usados fora da janela de 24h.
# Params posicionais: {{1}} contato · {{2}} referência da consulta · {{3}} médico · {{4}} data/hora
TEMPLATE_REMINDER = "taxa_reserva_lembrete"
TEMPLATE_CANCEL = "taxa_reserva_cancelamento"

# Janela de atendimento do WhatsApp: fora dela, só template é entregável.
WHATSAPP_WINDOW_HOURS = 24
```

- [ ] **Step 3: Rodar a suíte do cron para garantir que nada quebrou com o import**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -q`
Expected: PASS (11 testes, 0 falhas)

- [ ] **Step 4: Commit**

```bash
git add scripts/send_payment_reminders.py
git commit -m "chore(payment-cron): constantes de template e janela de 24h"
```

---

## Task 2: `_window_open` — detecção da janela de 24h

**Files:**
- Modify: `scripts/send_payment_reminders.py` (adicionar função perto de `find_receipt_in_conversation`)
- Test: `tests/test_payment_reminders_cancel.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_payment_reminders_cancel.py`:

```python
# ── Janela de 24h do WhatsApp ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_window_open_true_when_recent_inbound():
    """Se o contato mandou mensagem dentro da janela, a conversa está aberta."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[{"created_at": "2026-08-14T11:00:00+00:00"}]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    assert await spr._window_open(client, "5581987415206@s.whatsapp.net", now) is True


@pytest.mark.asyncio
async def test_window_closed_when_no_recent_inbound():
    """Sem mensagem recente do contato, a janela está fechada."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    assert await spr._window_open(client, "5581987415206@s.whatsapp.net", now) is False


@pytest.mark.asyncio
async def test_window_uses_24h_cutoff_and_role_user():
    """O corte é now-24h (UTC) e só conta mensagem inbound (role='user')."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)  # 12:30 UTC

    await spr._window_open(client, "5581987415206@s.whatsapp.net", now)

    table.gte.assert_called_once_with("created_at", "2026-08-13T12:30:00+00:00")
    table.eq.assert_any_call("role", "user")


@pytest.mark.asyncio
async def test_window_checks_both_phone_variants():
    """Reaproveita _phone_variants: casa mensagem gravada com/sem o 9º dígito."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    await spr._window_open(client, "5581987415206@s.whatsapp.net", now)

    variants = table.in_.call_args[0][1]
    assert "5581987415206" in variants
    assert "558187415206" in variants


@pytest.mark.asyncio
async def test_window_closed_on_lookup_error():
    """Fail-safe: erro na consulta => tratar como fechada (força template)."""
    client, table = _client()
    table.execute = AsyncMock(side_effect=Exception("supabase down"))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    assert await spr._window_open(client, "5581987415206@s.whatsapp.net", now) is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k window -q`
Expected: FAIL com `AttributeError: module 'scripts.send_payment_reminders' has no attribute '_window_open'`

- [ ] **Step 3: Implementar `_window_open`**

Adicionar em `scripts/send_payment_reminders.py`, logo acima de `find_receipt_in_conversation`:

```python
async def _window_open(client, phone: str, now: datetime) -> bool:
    """True se o contato mandou alguma mensagem (role='user') nas últimas
    WHATSAPP_WINDOW_HOURS. Fora dessa janela, o Meta só entrega template aprovado
    — mensagem livre é aceita pelo Chatwoot mas descartada silenciosamente.

    Reaproveita a normalização de telefone de find_receipt_in_conversation
    (contacts.phone e messages.phone divergem no 9º dígito). Erro na consulta =>
    'fechada' (fail-safe: força o caminho de template, que é entregável)."""
    from app.database import _phone_variants

    cutoff = (now - timedelta(hours=WHATSAPP_WINDOW_HOURS)).astimezone(timezone.utc).isoformat()
    variants = _phone_variants(phone)
    try:
        res = await (
            client.from_("messages")
            .select("created_at")
            .in_("phone", variants)
            .eq("role", "user")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        print(f"  [window] lookup falhou para {variants}: {e} — assumindo janela fechada")
        return False
    return bool(res.data)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k window -q`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "feat(payment-cron): _window_open detecta janela de 24h do WhatsApp"
```

---

## Task 3: `_send_template` e `_consulta_ref` — envio de template e referência da consulta

**Files:**
- Modify: `scripts/send_payment_reminders.py` (adicionar as duas funções perto de `send_whatsapp`, linha ~72)
- Test: `tests/test_payment_reminders_cancel.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_payment_reminders_cancel.py`:

```python
# ── Referência da consulta (próprio-paciente vs responsável) ─────────────────

def test_consulta_ref_reminder():
    assert spr._consulta_ref("reminder", None) == "sua consulta"
    assert spr._consulta_ref("reminder", "Bento") == "a consulta de Bento"


def test_consulta_ref_cancel():
    assert spr._consulta_ref("cancel", None) == "da sua consulta"
    assert spr._consulta_ref("cancel", "Bento") == "da consulta de Bento"


# ── Envio de template via Chatwoot ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_template_builds_expected_payload():
    """_send_template resolve a conversa e chama send_template_message com o
    template, categoria UTILITY, idioma pt_BR e os 4 params posicionais."""
    body_params = {"1": "Mariana", "2": "a consulta de Bento", "3": "Dr. Júlio", "4": "27/08/2026 às 14:00"}

    with patch("app.chatwoot.find_or_create_conversation",
               new_callable=AsyncMock, return_value=4321) as mock_conv, \
         patch("app.chatwoot.send_template_message", new_callable=AsyncMock) as mock_tpl:
        await spr._send_template("5581999767413", spr.TEMPLATE_REMINDER, body_params, "texto livre de fallback")

    mock_conv.assert_awaited_once_with("5581999767413@s.whatsapp.net")
    mock_tpl.assert_awaited_once_with(
        4321,
        template_name=spr.TEMPLATE_REMINDER,
        language="pt_BR",
        category="UTILITY",
        body_params=body_params,
        content="texto livre de fallback",
    )
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "consulta_ref or send_template" -q`
Expected: FAIL com `AttributeError: ... has no attribute '_consulta_ref'`

- [ ] **Step 3: Implementar as duas funções**

Adicionar em `scripts/send_payment_reminders.py`, logo abaixo de `send_whatsapp` (após a linha 72):

```python
def _consulta_ref(kind: str, patient_first: str | None) -> str:
    """Frase que vira o param {{2}} do template, espelhando os builders de texto
    livre. patient_first é None quando o contato é o próprio paciente."""
    if kind == "reminder":
        return f"a consulta de {patient_first}" if patient_first else "sua consulta"
    return f"da consulta de {patient_first}" if patient_first else "da sua consulta"


async def _send_template(phone: str, template_name: str, body_params: dict, content: str) -> None:
    """Envia um template aprovado via Chatwoot (entregável fora da janela de 24h).
    Espelha scripts/send_appointment_reminders.py::send_reminder_template."""
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    await send_template_message(
        conv_id,
        template_name=template_name,
        language="pt_BR",
        category="UTILITY",
        body_params=body_params,
        content=content,
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "consulta_ref or send_template" -q`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "feat(payment-cron): _send_template e _consulta_ref (próprio-paciente vs responsável)"
```

---

## Task 4: `_notify` — roteamento híbrido

**Files:**
- Modify: `scripts/send_payment_reminders.py` (adicionar após `_send_template`)
- Test: `tests/test_payment_reminders_cancel.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_payment_reminders_cancel.py`:

```python
# ── Roteamento híbrido texto-livre / template ────────────────────────────────

@pytest.mark.asyncio
async def test_notify_uses_free_text_when_window_open():
    """Janela aberta: manda texto livre (send_whatsapp), não usa template."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl:
        ok = await spr._notify(client, "5581999767413", kind="reminder", free_text="oi livre",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is True
    mock_wpp.assert_awaited_once_with("5581999767413", "oi livre")
    mock_tpl.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_uses_template_when_window_closed():
    """Janela fechada: manda template com params corretos, não texto livre."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl:
        ok = await spr._notify(client, "5581999767413", kind="cancel", free_text="cancel livre",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is True
    mock_wpp.assert_not_awaited()
    mock_tpl.assert_awaited_once_with(
        "5581999767413",
        spr.TEMPLATE_CANCEL,
        {"1": "Mariana", "2": "da consulta de Bento", "3": "Dr. Júlio", "4": "27/08/2026 às 14:00"},
        "cancel livre",
    )


@pytest.mark.asyncio
async def test_notify_self_patient_uses_sua_consulta():
    """Contato é o próprio paciente (patient_first=None) => {{2}} = 'sua consulta'."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl:
        await spr._notify(client, "5581996503841", kind="reminder", free_text="x",
                          contact_first="João", patient_first=None,
                          doctor_label="Dra. Bruna", date_str="28/08/2026 às 10:00", now=now)

    assert mock_tpl.await_args.args[2]["2"] == "sua consulta"


@pytest.mark.asyncio
async def test_notify_returns_false_on_send_failure():
    """Falha no envio (ex.: template ainda não aprovado) => retorna False, para o
    guard do cancelamento adiar em vez de cancelar silenciosamente."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template",
               new_callable=AsyncMock, side_effect=Exception("template not approved")):
        ok = await spr._notify(client, "5581999767413", kind="cancel", free_text="x",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k notify -q`
Expected: FAIL com `AttributeError: ... has no attribute '_notify'`

- [ ] **Step 3: Implementar `_notify`**

Adicionar em `scripts/send_payment_reminders.py`, logo abaixo de `_send_template`:

```python
async def _notify(client, phone: str, *, kind: str, free_text: str,
                  contact_first: str, patient_first: str | None,
                  doctor_label: str, date_str: str, now: datetime) -> bool:
    """Notifica um contato: texto livre dentro da janela de 24h, template
    aprovado fora dela. Retorna True se o envio teve sucesso.

    kind='reminder' | 'cancel'. free_text é a mensagem livre já montada pelo
    builder correspondente (usada dentro da janela e como `content` do template)."""
    template_name = TEMPLATE_REMINDER if kind == "reminder" else TEMPLATE_CANCEL
    body_params = {
        "1": contact_first,
        "2": _consulta_ref(kind, patient_first),
        "3": doctor_label,
        "4": date_str,
    }
    try:
        if await _window_open(client, phone, now):
            await send_whatsapp(phone, free_text)
        else:
            await _send_template(phone, template_name, body_params, free_text)
        print(f"  [{kind}] enviado para {phone}")
        return True
    except Exception as e:
        print(f"  [{kind}] FALHOU para {phone}: {e}")
        return False
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k notify -q`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "feat(payment-cron): _notify roteia texto-livre (janela aberta) vs template (fechada)"
```

---

## Task 5: Ligar `_send_payment_reminder` ao `_notify`

**Files:**
- Modify: `scripts/send_payment_reminders.py` (bloco de envio dentro de `_send_payment_reminder`, ~linhas 326-344)
- Test: `tests/test_payment_reminders_cancel.py` (atualizar 2 testes existentes)

- [ ] **Step 1: Preparar os testes existentes e escrever o novo (red)**

(a) No `test_reminder_sent_when_no_receipt_in_conversation`, adicionar o patch de
janela aberta ao bloco `with` (para o teste continuar exercendo `send_whatsapp`),
logo após o patch de `get_financial_contacts`:

```python
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
```

(`test_reminder_deferred_when_receipt_lookup_fails` bloqueia antes de `_notify` —
não precisa mudar.)

(b) Adicionar o teste novo do comportamento fora da janela ao fim de
`tests/test_payment_reminders_cancel.py`:

```python
@pytest.mark.asyncio
async def test_reminder_uses_template_out_of_window():
    """Fora da janela de 24h, o lembrete de cobrança vai por template (entregável)
    e payment_reminder_sent_at é marcado."""
    client, table = _client()
    now = datetime(2026, 8, 12, 12, 27, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._send_payment_reminder(client, appt, None, now)

    mock_tpl.assert_awaited_once()
    assert mock_tpl.await_args.args[1] == spr.TEMPLATE_REMINDER
    mock_wpp.assert_not_awaited()
    table.update.assert_called_once()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "reminder_uses_template_out_of_window" -q`
Expected: FAIL — hoje `_send_payment_reminder` chama `send_whatsapp` direto (não `_send_template`), então `mock_tpl` não é chamado.

- [ ] **Step 3: Trocar o bloco de envio para usar `_notify`**

Em `_send_payment_reminder`, substituir o trecho (linhas ~326-344):

```python
    any_sent = False
    for contact in financial_contacts:
        phone = contact["phone"]
        from app.utils import display_name as _dn
        contact_first = _dn(contact["name"] or patient_name)
        # Show patient name separately only when contact and patient differ
        patient_first = _dn(patient_name) if contact["name"] and contact["name"] != patient_name else None
        message = payment_reminder_message(contact_first, doctor_label, date_str, patient_first)
        try:
            await send_whatsapp(phone, message)
            any_sent = True
            print(f"  [payment_reminder] Sent to {phone} — {patient_name}")
        except Exception as e:
            print(f"  [payment_reminder] Failed to send to {phone}: {e}")
        if graph:
            try:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            except Exception as e:
                print(f"  [payment_reminder] save_to_checkpoint failed (non-fatal): {e}")
```

por:

```python
    any_sent = False
    for contact in financial_contacts:
        phone = contact["phone"]
        from app.utils import display_name as _dn
        contact_first = _dn(contact["name"] or patient_name)
        # Show patient name separately only when contact and patient differ
        patient_first = _dn(patient_name) if contact["name"] and contact["name"] != patient_name else None
        message = payment_reminder_message(contact_first, doctor_label, date_str, patient_first)
        sent = await _notify(client, phone, kind="reminder", free_text=message,
                             contact_first=contact_first, patient_first=patient_first,
                             doctor_label=doctor_label, date_str=date_str, now=now)
        any_sent = any_sent or sent
        if graph:
            try:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            except Exception as e:
                print(f"  [payment_reminder] save_to_checkpoint failed (non-fatal): {e}")
```

- [ ] **Step 4: Rodar os testes do lembrete**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "reminder" -q`
Expected: PASS (todos os testes de lembrete, incluindo cortesia e comprovante)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "feat(payment-cron): lembrete de taxa usa envio híbrido via _notify"
```

---

## Task 6: Ligar `_cancel_unpaid_appointment` ao `_notify`

**Files:**
- Modify: `scripts/send_payment_reminders.py` (bloco de envio dentro de `_cancel_unpaid_appointment`, ~linhas 396-415)
- Test: `tests/test_payment_reminders_cancel.py` (atualizar 2 testes existentes + 1 novo)

- [ ] **Step 1: Atualizar os testes existentes de cancelamento que passam pela janela**

Em `test_cancel_logs_appointment_canceled_event_per_notified_contact` e
`test_cancel_does_not_log_event_when_no_contact_notified`, adicionar ao bloco `with`
o patch de janela aberta (para continuarem exercendo o caminho `send_whatsapp`):

```python
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
```

(inserir logo após o patch de `get_financial_contacts` em cada um dos dois testes).

- [ ] **Step 2: Escrever o teste novo do bug corrigido (janela fechada)**

Adicionar ao fim de `tests/test_payment_reminders_cancel.py`:

```python
@pytest.mark.asyncio
async def test_cancel_uses_template_out_of_window_and_still_cancels():
    """Fora da janela de 24h, o aviso de cancelamento vai por template (entregável)
    e a consulta é cancelada normalmente — sem cair no cancelamento silencioso."""
    client, table = _client()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_tpl.assert_awaited_once()
    assert mock_tpl.await_args.args[1] == spr.TEMPLATE_CANCEL
    mock_wpp.assert_not_awaited()
    mock_cal.assert_awaited_once()
    table.update.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_deferred_when_template_send_fails():
    """Se o template ainda não existe/aprova, o envio falha, ninguém é notificado e
    a consulta NÃO é cancelada (adiada) — em vez de cancelar em silêncio."""
    client, table = _client()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template",
               new_callable=AsyncMock, side_effect=Exception("template not approved")), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event:
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_cal.assert_not_awaited()
    table.update.assert_not_called()
    mock_log_event.assert_not_awaited()
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "cancel_uses_template or cancel_deferred_when_template" -q`
Expected: FAIL — hoje `_cancel_unpaid_appointment` chama `send_whatsapp` direto (não `_send_template`), então `mock_tpl` não é chamado.

- [ ] **Step 4: Trocar o bloco de envio para usar `_notify`**

Em `_cancel_unpaid_appointment`, substituir o trecho (linhas ~396-415):

```python
    # Must notify at least one contact before canceling
    any_notified = False
    notified_phones = []
    for contact in financial_contacts:
        phone = contact["phone"]
        from app.utils import display_name as _dn
        contact_first = _dn(contact["name"] or patient_name)
        patient_first = _dn(patient_name) if contact["name"] and contact["name"] != patient_name else None
        message = payment_cancel_message(contact_first, doctor_label, date_str, patient_first)
        try:
            await send_whatsapp(phone, message)
            any_notified = True
            notified_phones.append(phone)
            print(f"  [payment_cancel] WhatsApp enviado para {phone}.")
        except Exception as e:
            print(f"  [payment_cancel] WhatsApp FALHOU para {phone}: {e}")
        if graph:
            try:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            except Exception as e:
                print(f"  [payment_cancel] save_to_checkpoint failed (non-fatal): {e}")
```

por:

```python
    # Must notify at least one contact before canceling
    any_notified = False
    notified_phones = []
    for contact in financial_contacts:
        phone = contact["phone"]
        from app.utils import display_name as _dn
        contact_first = _dn(contact["name"] or patient_name)
        patient_first = _dn(patient_name) if contact["name"] and contact["name"] != patient_name else None
        message = payment_cancel_message(contact_first, doctor_label, date_str, patient_first)
        sent = await _notify(client, phone, kind="cancel", free_text=message,
                             contact_first=contact_first, patient_first=patient_first,
                             doctor_label=doctor_label, date_str=date_str, now=now)
        if sent:
            any_notified = True
            notified_phones.append(phone)
        if graph:
            try:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            except Exception as e:
                print(f"  [payment_cancel] save_to_checkpoint failed (non-fatal): {e}")
```

- [ ] **Step 5: Rodar os testes de cancelamento**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "cancel" -q`
Expected: PASS (todos os testes de cancelamento)

- [ ] **Step 6: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "fix(payment-cron): cancelamento usa template fora da janela (fim do cancelamento silencioso)"
```

---

## Task 7: Verificação final da suíte completa

**Files:** nenhuma alteração — só verificação.

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short -q`
Expected: PASS — todos os testes (382 baseline + os novos deste plano), 0 falhas.

- [ ] **Step 2: Se algo falhar, corrigir antes de prosseguir**

Investigar cada falha (usar superpowers:systematic-debugging se necessário). Não seguir com falhas.

- [ ] **Step 3: Commit final (se houver ajuste) e pronto para revisão/PR**

```bash
git add -A
git commit -m "test(payment-cron): suíte completa verde com envio híbrido" || echo "nada a commitar"
```

---

## Notas de ativação (fora do código)

- A clínica precisa criar e aprovar no Meta os templates `taxa_reserva_lembrete` e
  `taxa_reserva_cancelamento` (UTILITY/pt_BR), com o corpo e os params do spec.
- Enquanto não aprovados: dentro da janela tudo funciona (texto livre); fora da
  janela `_send_template` falha → `_notify` retorna False → lembrete/cancelamento
  são **adiados** (nunca cancelamento silencioso). Nenhuma regressão frente ao
  estado atual.
- Os 7 pacientes já afetados são tratados manualmente pela clínica (fora de escopo).
