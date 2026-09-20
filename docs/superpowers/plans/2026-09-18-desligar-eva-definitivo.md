# Desligar a Eva em definitivo — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar à atendente um botão no painel que desliga em definitivo toda comunicação automática da Eva com um paciente (proativa e resposta ao vivo), reusando o campo `manual_hold`.

**Architecture:** O `manual_hold` já silencia o recebimento pelo portão `_eva_paused_for_phone`. Este plano faz os crons proativos respeitarem o `manual_hold` (filtrando destinatários) e adiciona no painel da atendente um botão que liga/desliga o `manual_hold` em todos os contatos do paciente, com um selo de estado bem visível.

**Tech Stack:** Python, FastAPI, Supabase (postgrest-py), pytest, template HTML com JS puro (dashboard da atendente).

---

## Estrutura de arquivos

Envio proativo (fazer respeitar `manual_hold`):
- `app/patients.py` — helper `_is_held` / `drop_manual_hold` e filtro em `return_reminder_contacts` e `consultation_reminder_contacts`.
- `scripts/send_no_show_messages.py` — filtra os contatos do no-show.
- `scripts/complete_appointments.py` — filtra os destinatários do pós-consulta.
- `scripts/send_payment_reminders.py` — filtra contatos financeiros e o contato que agendou.
- `scripts/send_scheduling_stall_nudges.py` — pula quando `manual_hold`.

Painel da atendente (o botão):
- `dashboard/attendant_db.py` — `set_patient_eva_off` e `is_patient_eva_off`.
- `dashboard/attendant_routes.py` — expõe `eva_off` no GET do paciente e cria o POST que liga/desliga.
- `dashboard/templates/atendente.html` — selo de estado e botões, mais o JS.

Fora de escopo (não recebem paciente ou já cobertos): `send_appointment_reminders.py` usa `consultation_reminder_contacts` (coberto pela Task 1); `doctor_daily_agenda` fala com o médico; `send_pending_payments_reminder` manda e-mail à clínica; `send_recesso_julio` é one-off; o relatório de stall manda e-mail à clínica; o recebimento ao vivo já está coberto por `_eva_paused_for_phone`.

Nota de comportamento (registrada, não é bug): no cron de pagamento, quando não há destinatário notificado, o cancelamento automático é adiado (`send_payment_reminders.py` já faz isso, "cancelamento adiado"). Logo, silenciar um paciente com taxa em aberto NÃO cancela a consulta dele; a clínica passa a cuidar disso na mão. É o esperado para quem teve a Eva desligada.

---

## Task 1: Filtro de `manual_hold` nos montadores de lembrete

**Files:**
- Modify: `app/patients.py` (add helpers; edit `return_reminder_contacts` e `consultation_reminder_contacts`)
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_patients.py`:

```python
# ── manual_hold: silêncio total dos lembretes proativos ──────────────────────

def test_drop_manual_hold_remove_so_os_em_hold():
    contatos = [
        {"id": "c1", "phone": "5581111", "manual_hold": False},
        {"id": "c2", "phone": "5581222", "manual_hold": True},
        {"id": "c3", "phone": "5581333"},  # ausente == não-hold
    ]
    out = patients.drop_manual_hold(contatos)
    assert {c["id"] for c in out} == {"c1", "c3"}


@pytest.mark.asyncio
async def test_return_reminder_contacts_pula_manual_hold():
    linked = [
        {"contact": {"id": "cmae", "phone": "5581999", "manual_hold": False},
         "is_self": False, "relationship": "mãe"},
        {"contact": {"id": "cpai", "phone": "5581888", "manual_hold": True},
         "is_self": False, "relationship": "pai"},
    ]
    with patch("app.patients.get_patient_by_id", new_callable=AsyncMock,
               return_value={"birth_date": "2015-01-01"}), \
         patch("app.patients._linked_contacts_with_marker", new_callable=AsyncMock,
               return_value=linked):
        out = await patients.return_reminder_contacts("p1")
    assert {c["id"] for c in out} == {"cmae"}


@pytest.mark.asyncio
async def test_consultation_reminder_contacts_pula_booking_em_hold():
    with patch("app.patients.get_patient_by_id", new_callable=AsyncMock,
               return_value={"birth_date": "2015-01-01"}), \
         patch("app.patients._linked_contacts_with_marker", new_callable=AsyncMock,
               return_value=[]), \
         patch("app.patients.get_contact_by_id", new_callable=AsyncMock,
               return_value={"id": "cbook", "phone": "5581777",
                             "active": True, "manual_hold": True}):
        out = await patients.consultation_reminder_contacts("p1", {"contact_id": "cbook"})
    assert out == []
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/test_patients.py -k "manual_hold or pula" -v`
Expected: FAIL — `AttributeError: module 'app.patients' has no attribute 'drop_manual_hold'`.

- [ ] **Step 3: Implementar o filtro**

Em `app/patients.py`, adicionar os helpers logo antes de `async def get_contacts_for_patient` (perto da linha 93):

```python
def _is_held(contact: dict | None) -> bool:
    """True quando a Eva foi desligada em definitivo para este contato."""
    return bool(contact and contact.get("manual_hold"))


def drop_manual_hold(contacts: list[dict]) -> list[dict]:
    """Remove da lista os contatos em manual_hold (Eva desligada).

    Usado pelos montadores de destinatário de mensagem PROATIVA (lembretes de
    retorno/consulta, no-show, pós-consulta, taxa). O recebimento ao vivo já é
    barrado antes, no portão _eva_paused_for_phone (app/main.py)."""
    return [c for c in contacts if not _is_held(c)]
```

Em `return_reminder_contacts`, filtrar a lista `linked` logo depois de obtê-la:

```python
    linked = await _linked_contacts_with_marker(patient_id, include_inactive=include_inactive)
    linked = [e for e in linked if not _is_held(e["contact"])]
```

Em `consultation_reminder_contacts`, filtrar `linked` do mesmo jeito e blindar o desvio do contato que agendou:

```python
    linked = await _linked_contacts_with_marker(patient_id, include_inactive=include_inactive)
    linked = [e for e in linked if not _is_held(e["contact"])]

    own = [
        lc["contact"] for lc in linked
        if lc["is_self"] and _is_self_like(lc["relationship"])
    ]
    if age is not None and age >= 18 and own:
        return own

    booking = await get_contact_by_id((appointment or {}).get("contact_id"))
    if booking and not _is_held(booking) and (include_inactive or booking.get("active")):
        return [booking]

    return [lc["contact"] for lc in linked]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/test_patients.py -k "manual_hold or pula" -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Rodar o arquivo inteiro (não quebrar o resto)**

Run: `uv run pytest tests/test_patients.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(patients): lembretes de retorno/consulta pulam contato em manual_hold"
```

---

## Task 2: No-show respeita `manual_hold`

**Files:**
- Modify: `scripts/send_no_show_messages.py`
- Test: `tests/test_send_no_show_messages.py`

- [ ] **Step 1: Escrever o teste que falha**

O envio é feito por `sns.process(client)`, que busca os agendamentos no cliente fake `_fake_client` (já usado no arquivo) e chama `send_no_show_message(phone, first_name)` para cada contato. Adicionar ao final de `tests/test_send_no_show_messages.py`:

```python
def test_no_show_pula_contato_em_manual_hold(monkeypatch):
    appts = [
        {"id": "r1", "appointment_id": "a1", "patient_id": "p1",
         "status": "no_show", "no_show_message_sent_at": None,
         "start_time": "2026-07-01T12:00:00+00:00", "patients": {"name": "Carlos Silva"}},
    ]
    client = _fake_client(appts)
    send = AsyncMock()
    monkeypatch.setattr(sns, "send_no_show_message", send)
    monkeypatch.setattr(sns, "get_contacts_for_patient", AsyncMock(return_value=[
        {"phone": "5581111", "manual_hold": False},
        {"phone": "5581222", "manual_hold": True},
    ]))

    sent = asyncio.run(sns.process(client))

    assert sent == 1
    send.assert_awaited_once_with("5581111", "Carlos")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/test_send_no_show_messages.py -k manual_hold -v`
Expected: FAIL — o contato em hold recebe mensagem (lista `enviados` com dois telefones).

- [ ] **Step 3: Implementar o filtro**

Em `scripts/send_no_show_messages.py`, garantir o import no topo:

```python
from app.patients import get_contacts_for_patient, drop_manual_hold
```

E filtrar logo após buscar os contatos (perto da linha 66):

```python
        contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
        contacts = drop_manual_hold(contacts)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/test_send_no_show_messages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_no_show_messages.py tests/test_send_no_show_messages.py
git commit -m "feat(no-show): pula contato em manual_hold"
```

---

## Task 3: Pós-consulta respeita `manual_hold`

**Files:**
- Modify: `scripts/complete_appointments.py`
- Test: `tests/test_complete_appointments.py`

- [ ] **Step 1: Escrever o teste que falha**

O envio por agendamento é `ca._process_pos_consulta(client, appt, now_iso)`, que chama `get_contacts_for_patient(patient_id, "consulta")` e envia via `send_pos_consulta(phone, first_name)` para TODOS os contatos. Os helpers `_client`, `_appt` e `NOW_ISO` já existem no arquivo. Adicionar ao final de `tests/test_complete_appointments.py`:

```python
@pytest.mark.asyncio
async def test_pos_consulta_pula_contato_em_manual_hold():
    client, table = _client()
    with patch("scripts.complete_appointments.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[
                   {"phone": "5581111", "manual_hold": False},
                   {"phone": "5581222", "manual_hold": True},
               ]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_send.assert_awaited_once_with("5581111", "Natalia")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/test_complete_appointments.py -k manual_hold -v`
Expected: FAIL — contato em hold recebe pós-consulta (mock_send chamado duas vezes).

- [ ] **Step 3: Implementar o filtro**

Em `scripts/complete_appointments.py`, garantir o import:

```python
from app.patients import get_contacts_for_patient, drop_manual_hold
```

E filtrar logo após a busca (perto da linha 102):

```python
    contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
    contacts = drop_manual_hold(contacts)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/test_complete_appointments.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/complete_appointments.py tests/test_complete_appointments.py
git commit -m "feat(pos-consulta): pula contato em manual_hold"
```

---

## Task 4: Lembrete/cancelamento de taxa respeita `manual_hold`

**Files:**
- Modify: `scripts/send_payment_reminders.py` (`get_financial_contacts`, `_reminder_recipients`)
- Test: `tests/test_payment_reminders_cancel.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_payment_reminders_cancel.py`:

```python
import scripts.send_payment_reminders as pay
from unittest.mock import AsyncMock, MagicMock, patch


def _client_pc(rows):
    execute = AsyncMock(return_value=MagicMock(data=rows))
    table = MagicMock()
    for m in ("select", "eq", "in_", "order", "limit"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client


@pytest.mark.asyncio
async def test_get_financial_contacts_pula_manual_hold():
    client = _client_pc([
        {"role": "financeiro", "contacts": {"phone": "5581111", "name": "OK", "manual_hold": False}},
        {"role": "financeiro", "contacts": {"phone": "5581222", "name": "Hold", "manual_hold": True}},
    ])
    out = await pay.get_financial_contacts(client, "p1")
    assert [c["phone"] for c in out] == ["5581111"]


@pytest.mark.asyncio
async def test_reminder_recipients_ignora_booking_em_hold():
    financeiros = [{"phone": "5581111", "name": "Fin"}]
    with patch("scripts.send_payment_reminders.get_contact_by_id",
               new_callable=AsyncMock,
               return_value={"id": "cb", "phone": "5581999", "manual_hold": True}):
        out = await pay._reminder_recipients({"contact_id": "cb"}, financeiros)
    # booking em hold é ignorado → cai no fallback dos financeiros (já filtrados)
    assert [c["phone"] for c in out] == ["5581111"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "manual_hold or booking_em_hold" -v`
Expected: FAIL — `get_financial_contacts` devolve os dois; booking em hold é devolvido.

- [ ] **Step 3: Implementar o filtro**

Em `scripts/send_payment_reminders.py`, `get_financial_contacts`: incluir `manual_hold` no select e pular quem estiver em hold.

```python
async def get_financial_contacts(client, patient_id: str) -> list[dict]:
    """Return all contacts with role 'financeiro' for a patient (phone + name).

    Pula contatos em manual_hold: a Eva foi desligada em definitivo para eles."""
    result = await (
        client.from_("patient_contacts")
        .select("role, contacts(phone, name, manual_hold)")
        .eq("patient_id", patient_id)
        .eq("role", "financeiro")
        .execute()
    )
    contacts = []
    for row in result.data or []:
        c = row.get("contacts") or {}
        if c.get("manual_hold"):
            continue
        if c.get("phone"):
            contacts.append({"phone": c["phone"], "name": c.get("name", "")})
    return contacts
```

Em `_reminder_recipients`, ignorar o contato que agendou quando estiver em hold:

```python
    booking = await get_contact_by_id(appt.get("contact_id"))
    if booking and booking.get("phone") and not booking.get("manual_hold"):
        return [{"phone": booking["phone"], "name": booking.get("name")}]
    return financial_contacts
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "feat(taxa): lembrete/cancelamento pula contato em manual_hold"
```

---

## Task 5: Nudge de agendamento respeita `manual_hold`

**Files:**
- Modify: `scripts/send_scheduling_stall_nudges.py` (`_send_nudge`)
- Test: `tests/test_scheduling_stall_crons.py`

- [ ] **Step 1: Escrever o teste que falha**

Espelho exato de `test_no_nudge_for_inactive_patient` já presente no arquivo, trocando o flag. `nud`, `_case` e `TZ_NOW` já existem em `tests/test_scheduling_stall_crons.py`. Adicionar:

```python
@pytest.mark.asyncio
async def test_no_nudge_for_manual_hold_patient():
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"active": True, "manual_hold": True, "name": "João"}), \
         patch.object(nud, "_window_open", new_callable=AsyncMock, return_value=True), \
         patch.object(nud, "send_whatsapp", new_callable=AsyncMock) as send, \
         patch.object(nud, "mark_handled", new_callable=AsyncMock) as mark:
        await nud._send_nudge(MagicMock(), None, _case(), TZ_NOW)
    send.assert_not_awaited()
    mark.assert_not_awaited()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/test_scheduling_stall_crons.py -k manual_hold -v`
Expected: FAIL — o nudge é enviado mesmo com `manual_hold`.

- [ ] **Step 3: Implementar a guarda**

Em `scripts/send_scheduling_stall_nudges.py`, dentro de `_send_nudge`, logo após a linha `if not user.get("active"): return`:

```python
    if not user.get("active"):
        return  # eva-inativa/pausado → e-mail da clínica cuida
    if user.get("manual_hold"):
        return  # Eva desligada em definitivo para este contato
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `uv run pytest tests/test_scheduling_stall_crons.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_scheduling_stall_nudges.py tests/test_scheduling_stall_crons.py
git commit -m "feat(nudge): pula contato em manual_hold"
```

---

## Task 6: Camada de dados do painel (liga/desliga por paciente)

**Files:**
- Modify: `dashboard/attendant_db.py`
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `dashboard/tests/test_attendant_db.py`:

```python
async def test_set_patient_eva_off_liga_em_todos_os_contatos(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "role": "agendamento"},
        {"patient_id": "p1", "contact_id": "c2", "role": "financeiro"},
    ]
    patched_client.store["contacts"] = [
        {"id": "c1", "manual_hold": False},
        {"id": "c2", "manual_hold": False},
    ]
    n = await attendant_db.set_patient_eva_off("p1", True)
    assert n == 2
    assert all(c["manual_hold"] for c in patched_client.store["contacts"])


async def test_set_patient_eva_off_religa(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "role": "agendamento"},
    ]
    patched_client.store["contacts"] = [{"id": "c1", "manual_hold": True}]
    await attendant_db.set_patient_eva_off("p1", False)
    assert patched_client.store["contacts"][0]["manual_hold"] is False


async def test_is_patient_eva_off_true_quando_algum_em_hold(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "contacts": {"manual_hold": False}},
        {"patient_id": "p1", "contact_id": "c2", "contacts": {"manual_hold": True}},
    ]
    assert await attendant_db.is_patient_eva_off("p1") is True


async def test_is_patient_eva_off_false_quando_nenhum(patched_client):
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contact_id": "c1", "contacts": {"manual_hold": False}},
    ]
    assert await attendant_db.is_patient_eva_off("p1") is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -k eva_off -v; cd ..`
Expected: FAIL — `AttributeError: module 'attendant_db' has no attribute 'set_patient_eva_off'`.

- [ ] **Step 3: Implementar as funções**

Em `dashboard/attendant_db.py`, adicionar (perto das outras operações de escrita/leitura de contato):

```python
async def set_patient_eva_off(patient_id: str, off: bool) -> int:
    """Liga/desliga a Eva em definitivo para o paciente INTEIRO.

    Grava manual_hold=off em TODOS os contatos vinculados ao paciente. Retorna
    quantos contatos foram afetados. Reversível: off=False religa a Eva."""
    client = await get_client()
    res = await (
        client.from_("patient_contacts")
        .select("contact_id")
        .eq("patient_id", patient_id)
        .execute()
    )
    contact_ids = sorted({r["contact_id"] for r in (res.data or []) if r.get("contact_id")})
    for cid in contact_ids:
        await client.from_("contacts").update({"manual_hold": off}).eq("id", cid).execute()
    return len(contact_ids)


async def is_patient_eva_off(patient_id: str) -> bool:
    """True se algum contato vinculado ao paciente está em manual_hold
    (Eva desligada em definitivo)."""
    client = await get_client()
    res = await (
        client.from_("patient_contacts")
        .select("contacts(manual_hold)")
        .eq("patient_id", patient_id)
        .execute()
    )
    for row in (res.data or []):
        c = row.get("contacts") or {}
        if c.get("manual_hold"):
            return True
    return False
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -k eva_off -v; cd ..`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel-db): set_patient_eva_off e is_patient_eva_off"
```

---

## Task 7: Endpoints do painel (estado + liga/desliga)

**Files:**
- Modify: `dashboard/attendant_routes.py` (GET `/paciente/{id}` ganha `eva_off`; novo POST `/paciente/{id}/eva`)
- Test: `dashboard/tests/test_attendant_routes.py`, `dashboard/tests/test_attendant_scope.py`

- [ ] **Step 1: Escrever os testes que falham**

Em `test_attendant_routes.py` a fixture autouse `_bypass_scope` já neutraliza os guards de escopo e existe a fixture `client`; o foco aqui é o comportamento da rota. Adicionar:

```python
def test_desligar_eva_liga_manual_hold(client, monkeypatch):
    chamado = {}
    async def _set(pid, off):
        chamado["args"] = (pid, off); return 2
    async def _log(*a, **k):
        return None
    monkeypatch.setattr(attendant_db, "set_patient_eva_off", _set)
    monkeypatch.setattr(attendant_db, "log_event", _log)

    r = client.post("/api/atendente/paciente/p1/eva",
                    params={"token": "test-token"},
                    json={"phone": "5581999", "data": {"off": True}})
    assert r.status_code == 200
    assert r.json()["afetados"] == 2
    assert chamado["args"] == ("p1", True)
```

Em `test_attendant_scope.py` já existem o helper `_scope(monkeypatch, contact_id, patient_ids)`, `_noop`, `TOKEN` e `PHONE`. Espelhando `test_update_paciente_fora_do_escopo_recusa`, adicionar:

```python
def test_eva_off_fora_do_escopo_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    called = {"n": 0}
    async def fake_set(pid, off):
        called["n"] += 1
        return 0
    monkeypatch.setattr(attendant_db, "set_patient_eva_off", fake_set)
    monkeypatch.setattr(attendant_db, "log_event", _noop)
    r = client.post("/api/atendente/paciente/p_ALHEIO/eva", params=TOKEN,
                    json={"phone": PHONE, "data": {"off": True}})
    assert r.status_code == 403
    assert called["n"] == 0
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py tests/test_attendant_scope.py -k eva -v; cd ..`
Expected: FAIL — rota `/paciente/{id}/eva` não existe (404).

- [ ] **Step 3: Implementar os endpoints**

Em `dashboard/attendant_routes.py`, no GET do paciente, incluir `eva_off` na resposta:

```python
@router.get("/paciente/{patient_id}")
async def paciente(patient_id: str, contact_id: str, _: None = Depends(verify_token)):
    patient = await attendant_db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="paciente não encontrado")
    link = await attendant_db.get_link(patient_id, contact_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)
    return_reminder = await attendant_db.get_return_reminder(patient_id)
    eva_off = await attendant_db.is_patient_eva_off(patient_id)
    return {"patient": patient, "link": link, "return_reminder": return_reminder,
            "eva_off": eva_off}
```

E adicionar o POST logo após `update_paciente`:

```python
@router.post("/paciente/{patient_id}/eva")
async def set_eva(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    """Liga/desliga a Eva em definitivo para o paciente inteiro (manual_hold em
    todos os contatos). body.data = {"off": true|false}."""
    await _assert_patient_scope(body.phone, patient_id)
    off = bool(body.data.get("off"))
    afetados = await attendant_db.set_patient_eva_off(patient_id, off)
    await attendant_db.log_event("attendant_set_eva_off", body.phone,
                                 {"patient_id": patient_id, "off": off, "afetados": afetados})
    return {"ok": True, "off": off, "afetados": afetados}
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py tests/test_attendant_scope.py -k eva -v; cd ..`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py dashboard/tests/test_attendant_scope.py
git commit -m "feat(painel-api): estado eva_off e endpoint que liga/desliga a Eva"
```

---

## Task 8: Botão e selo no painel da atendente

**Files:**
- Modify: `dashboard/templates/atendente.html`

Sem teste automatizado (template com JS). A verificação é manual, descrita no fim da Task.

- [ ] **Step 1: Passar `eva_off` para o render**

Em `loadPatient`, incluir o novo campo e repassar:

```javascript
async function loadPatient(pid) {
  const r = await fetch(`/api/atendente/paciente/${pid}?contact_id=${CONTACT.id}&token=${encodeURIComponent(TOKEN)}`);
  if (!r.ok) { setStatus("Erro ao carregar paciente."); return; }
  const { patient, link, return_reminder, eva_off } = await r.json();
  renderForms(patient, link, return_reminder, eva_off);
}
```

E na assinatura do render:

```javascript
function renderForms(patient, link, returnReminder, evaOff) {
```

- [ ] **Step 2: Bloco do selo/botão**

Adicionar um helper de bloco perto de `returnBlock` (antes de `renderForms`):

```javascript
function evaBlock(pid, off) {
  if (off) {
    return `<div class="mt-3 rounded-md border border-red-300 bg-red-50 dark:border-red-700 dark:bg-red-900/30 p-3 flex items-center justify-between gap-3">
      <div>
        <div class="text-sm font-semibold text-red-700 dark:text-red-300">🔕 Eva desligada</div>
        <div class="text-xs text-red-600 dark:text-red-400">sem lembretes e sem respostas</div>
      </div>
      <button onclick="setEva('${pid}', false)" class="bg-gray-600 hover:bg-gray-700 text-white text-xs px-3 py-1.5 rounded whitespace-nowrap">Religar Eva</button>
    </div>`;
  }
  return `<div class="mt-3">
    <button onclick="setEva('${pid}', true)" class="bg-red-600 hover:bg-red-700 text-white text-xs px-3 py-1.5 rounded">Desligar Eva</button>
  </div>`;
}
```

- [ ] **Step 3: Inserir o bloco na seção Paciente**

Dentro de `pacienteHtml`, logo depois de `${returnBlock(patient.id, returnReminder)}` e antes do botão "Salvar paciente":

```javascript
      ${returnBlock(patient.id, returnReminder)}
      ${evaBlock(patient.id, evaOff)}
      <button onclick="savePatient('${patient.id}')" ...>Salvar paciente</button>
```

- [ ] **Step 4: Função `setEva`**

Adicionar perto das outras funções de salvar (usa o helper `post` já existente):

```javascript
async function setEva(pid, off) {
  const msg = off
    ? "Desligar a Eva em definitivo para este paciente? Ela para de mandar lembretes e de responder mensagens. Reversível pelo botão Religar."
    : "Religar a Eva para este paciente?";
  if (!confirm(msg)) return;
  const ok = await post(`/api/atendente/paciente/${pid}/eva`, { data: { off } });
  if (ok) { flash(off ? "Eva desligada ✓" : "Eva religada ✓"); loadPatient(pid); }
}
```

- [ ] **Step 5: Verificação manual**

Subir o dashboard localmente e abrir o painel com um telefone de teste (via `?phone=`), conferindo: com a Eva ligada aparece o botão vermelho "Desligar Eva"; ao clicar e confirmar, o selo "🔕 Eva desligada / sem lembretes e sem respostas" toma o lugar e aparece "Religar Eva"; ao religar, volta o botão vermelho.

Run: `cd dashboard && uv run uvicorn main:app --reload --port 8001` (abrir `http://localhost:8001/atendente?phone=<telefone_de_teste>&token=<ATTENDANT_PANEL_TOKEN>`)

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(painel-ui): botao Desligar/Religar Eva e selo de estado"
```

---

## Task 9: Suíte completa + fechamento

- [ ] **Step 1: Testes da app**

Run: `uv run pytest --tb=short`
Expected: PASS (atenção à ordem de arquivos do pytest citada no CLAUDE.md; não passe `test_patients.py` como primeiro argumento).

- [ ] **Step 2: Testes do dashboard**

Run: `cd dashboard && uv run pytest --tb=short; cd ..`
Expected: PASS.

- [ ] **Step 3: Conferir a cobertura contra o spec**

Reler `docs/superpowers/specs/2026-09-18-desligar-eva-definitivo-design.md` e confirmar que cada disparo proativo citado (retorno, consulta, taxa, no-show, nudge, pós-consulta) tem uma task que o cobre, que o recebimento já estava coberto, e que o botão liga/desliga por paciente inteiro está no painel.

- [ ] **Step 4: Abrir o PR**

Da worktree, empurrar a branch e abrir o PR descrevendo o reuso do `manual_hold`, a lista de crons ajustados e a ressalva do cancelamento adiado.
