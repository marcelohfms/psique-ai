# Lembretes só para o contato próprio do paciente adulto — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Para pacientes adultos (≥18) com número próprio cadastrado, lembretes de consulta e retorno vão só para o contato do próprio paciente; lembretes/cancelamento de taxa de reserva vão só para o contato que fez a reserva.

**Architecture:** Centraliza a lógica de idade + filtro `is_self` em dois helpers puros/async em `app/patients.py` (`_compute_age`, `get_reminder_contacts`, `get_contact_by_id`). Os três crons de lembrete passam a chamar esses helpers. A detecção de comprovante que bloqueia cancelamento indevido permanece varrendo todos os contatos financeiros.

**Tech Stack:** Python 3, asyncio, Supabase (postgrest async client), pytest + unittest.mock.

---

## File Structure

- `app/patients.py` — **Modify.** Adiciona `_compute_age`, `get_reminder_contacts`, `get_contact_by_id`. Não altera `get_contacts_for_patient` (usada em outros fluxos).
- `scripts/send_appointment_reminders.py` — **Modify.** Troca `get_contacts_for_patient` → `get_reminder_contacts` no envio de lembrete de consulta.
- `scripts/send_return_reminders.py` — **Modify.** Mesma troca no lembrete de retorno.
- `scripts/send_payment_reminders.py` — **Modify.** Destinatário do lembrete e do cancelamento passa a ser o contato da reserva (`appointments.contact_id`); adiciona `contact_id` ao `_appt_select`; guarda de comprovante permanece ampla.
- `tests/test_patients.py` — **Modify/Create tests.** Unit de `_compute_age` e `get_reminder_contacts`.
- `tests/test_reminders.py` — **Modify.** Casos de consulta (adulto/menor/sem-self/sem-DOB) via `get_reminder_contacts`.
- `tests/test_return_reminders_cron.py` — **Modify.** Caso adulto-com-self no retorno.
- `tests/test_payment_reminders_cancel.py` — **Modify.** Destinatário = contato da reserva; fallback; guarda ampla.

**Nota sobre pytest neste repo:** a ordem dos arquivos importa (import circular). NÃO passe `tests/test_patients.py` como primeiro argumento. Rode a suíte inteira com `uv run pytest --tb=short`, ou rode um teste específico por nó completo (`uv run pytest tests/test_reminders.py::nome -v`).

---

## Task 1: `_compute_age` — idade a partir de birth_date

**Files:**
- Modify: `app/patients.py` (adicionar função pura perto de `_birth_date_variants`)
- Test: `tests/test_patients.py`

- [ ] **Step 1: Write the failing test**

Adicione ao final de `tests/test_patients.py`:

```python
from datetime import date
from unittest.mock import patch
from app.patients import _compute_age


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 12)


def test_compute_age_ddmmyyyy():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("15/01/1990") == 36


def test_compute_age_iso():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("1990-01-15") == 36


def test_compute_age_exactly_18_on_birthday():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("12/08/2008") == 18


def test_compute_age_day_before_18th_birthday():
    with patch("app.patients.date", _FixedDate):
        assert _compute_age("13/08/2008") == 17


def test_compute_age_none_and_garbage():
    assert _compute_age(None) is None
    assert _compute_age("") is None
    assert _compute_age("não sei") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_patients.py::test_compute_age_ddmmyyyy -v`
Expected: FAIL — `ImportError: cannot import name '_compute_age'`.

- [ ] **Step 3: Write minimal implementation**

Em `app/patients.py`, garanta o import no topo (já existe `from datetime import datetime, timezone` — adicione `date`):

```python
from datetime import date, datetime, timezone
```

Adicione a função logo após `_birth_date_variants`:

```python
def _compute_age(birth_date: str | None) -> int | None:
    """Idade em anos completos a partir de `patients.birth_date`.

    Aceita as duas grafias que convivem no banco (dd/mm/aaaa do chat e ISO de
    imports). Retorna None quando ausente ou não parseável — o chamador trata
    None como "idade desconhecida" e NÃO suprime contatos nesse caso.
    """
    raw = (birth_date or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            bd = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None
    today = date.today()
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_patients.py -k compute_age -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(lembretes): _compute_age a partir de birth_date (dd/mm/aaaa ou ISO)"
```

---

## Task 2: `get_reminder_contacts` — filtro adulto+self

**Files:**
- Modify: `app/patients.py` (adicionar após `get_contacts_for_patient`)
- Test: `tests/test_patients.py`

**Regra:** adulto (`age >= 18`) **e** existe ao menos um contato `is_self=True` → retorna só os `is_self=True`. Caso contrário → todos (mesma semântica de `active`/`include_inactive` de `get_contacts_for_patient`).

- [ ] **Step 1: Write the failing test**

Adicione a `tests/test_patients.py`. O helper monta um cliente Supabase mockado que responde à cadeia `from_("patient_contacts").select(...).eq(...).eq(...).execute()` com as linhas de contato, e à cadeia `from_("patients").select(...).eq(...).execute()` com o birth_date.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch as _patch
from app.patients import get_reminder_contacts


def _reminder_client(pc_rows, patient_birth_date):
    """Cliente mock: patient_contacts.execute() -> pc_rows;
    patients.execute() -> [{'birth_date': ...}]."""
    pc_table = MagicMock()
    pc_table.select.return_value = pc_table
    pc_table.eq.return_value = pc_table
    pc_table.execute = AsyncMock(return_value=MagicMock(data=pc_rows))

    pat_table = MagicMock()
    pat_table.select.return_value = pat_table
    pat_table.eq.return_value = pat_table
    pat_table.execute = AsyncMock(
        return_value=MagicMock(data=[{"birth_date": patient_birth_date}])
    )

    client = MagicMock()
    def _from(name):
        return pc_table if name == "patient_contacts" else pat_table
    client.from_.side_effect = _from
    return client


def _pc(cid, phone, is_self, active=True):
    return {"contact_id": cid, "is_self": is_self,
            "contacts": {"id": cid, "phone": phone, "active": active}}


@pytest.mark.asyncio
async def test_reminder_contacts_adult_with_self_returns_only_self():
    rows = [_pc("c-self", "5581000", True), _pc("c-mae", "5581999", False)]
    client = _reminder_client(rows, "15/01/1990")
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await get_reminder_contacts("p1", "consulta", include_inactive=True)
    assert [c["phone"] for c in out] == ["5581000"]


@pytest.mark.asyncio
async def test_reminder_contacts_adult_without_self_returns_all():
    rows = [_pc("c-mae", "5581999", False), _pc("c-pai", "5581888", False)]
    client = _reminder_client(rows, "15/01/1990")
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await get_reminder_contacts("p1", "consulta", include_inactive=True)
    assert sorted(c["phone"] for c in out) == ["5581888", "5581999"]


@pytest.mark.asyncio
async def test_reminder_contacts_minor_with_self_returns_all():
    rows = [_pc("c-self", "5581000", True), _pc("c-mae", "5581999", False)]
    client = _reminder_client(rows, "12/08/2015")  # 10 anos em 2026
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await get_reminder_contacts("p1", "consulta", include_inactive=True)
    assert sorted(c["phone"] for c in out) == ["5581000", "5581999"]


@pytest.mark.asyncio
async def test_reminder_contacts_unknown_dob_returns_all():
    rows = [_pc("c-self", "5581000", True), _pc("c-mae", "5581999", False)]
    client = _reminder_client(rows, None)
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await get_reminder_contacts("p1", "consulta", include_inactive=True)
    assert sorted(c["phone"] for c in out) == ["5581000", "5581999"]


@pytest.mark.asyncio
async def test_reminder_contacts_excludes_inactive_by_default():
    rows = [_pc("c-self", "5581000", True, active=False),
            _pc("c-mae", "5581999", False, active=True)]
    client = _reminder_client(rows, "15/01/1990")
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await get_reminder_contacts("p1", "consulta", include_inactive=False)
    # self inativo é filtrado; sobra o responsável ativo (sem self ativo -> todos ativos)
    assert [c["phone"] for c in out] == ["5581999"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_patients.py -k reminder_contacts -v`
Expected: FAIL — `ImportError: cannot import name 'get_reminder_contacts'`.

- [ ] **Step 3: Write minimal implementation**

Em `app/patients.py`, adicione após `get_contacts_for_patient`:

```python
async def get_reminder_contacts(
    patient_id: str, role: str, include_inactive: bool = False
) -> list[dict]:
    """Contatos que devem receber um lembrete de consulta/retorno.

    Regra: paciente ADULTO (idade >= 18) que tem ao menos um contato próprio
    (is_self=True) recebe o lembrete SÓ nesse(s) contato(s) — os responsáveis
    são omitidos. Menor de idade, paciente sem contato próprio, ou birth_date
    ausente/imparseável caem no comportamento padrão: todos os contatos do
    papel (mesma semântica active/include_inactive de get_contacts_for_patient).
    """
    client = await get_supabase()
    result = (
        await client.from_("patient_contacts")
        .select("contact_id, is_self, contacts(*)")
        .eq("patient_id", patient_id)
        .eq("role", role)
        .execute()
    )

    seen: set[str] = set()
    rows: list[dict] = []
    for row in (result.data or []):
        contact = row.get("contacts")
        if contact and (include_inactive or contact.get("active")) and contact["id"] not in seen:
            seen.add(contact["id"])
            rows.append({"is_self": bool(row.get("is_self")), "contact": contact})

    patient = await get_patient_by_id(patient_id)
    age = _compute_age((patient or {}).get("birth_date"))

    self_contacts = [r["contact"] for r in rows if r["is_self"]]
    if age is not None and age >= 18 and self_contacts:
        return self_contacts
    return [r["contact"] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_patients.py -k reminder_contacts -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(lembretes): get_reminder_contacts filtra responsáveis p/ adulto com self"
```

---

## Task 3: `get_contact_by_id` — resolver contato da reserva

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Write the failing test**

```python
from app.patients import get_contact_by_id


@pytest.mark.asyncio
async def test_get_contact_by_id_found():
    table = MagicMock()
    table.select.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(
        return_value=MagicMock(data=[{"id": "c1", "phone": "5581000", "name": "Ana"}])
    )
    client = MagicMock()
    client.from_.return_value = table
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await get_contact_by_id("c1")
    assert out["phone"] == "5581000"


@pytest.mark.asyncio
async def test_get_contact_by_id_missing_returns_none():
    table = MagicMock()
    table.select.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        assert await get_contact_by_id("nope") is None
    assert await get_contact_by_id(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_patients.py -k get_contact_by_id -v`
Expected: FAIL — `ImportError: cannot import name 'get_contact_by_id'`.

- [ ] **Step 3: Write minimal implementation**

Em `app/patients.py`, adicione após `get_contact_by_phone`:

```python
async def get_contact_by_id(contact_id: str | None) -> dict | None:
    """Retorna a linha de `contacts` por id, ou None (inclui id None)."""
    if not contact_id:
        return None
    client = await get_supabase()
    result = (
        await client.from_("contacts")
        .select("*")
        .eq("id", contact_id)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_patients.py -k get_contact_by_id -v`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(lembretes): get_contact_by_id resolve o contato da reserva"
```

---

## Task 4: Lembrete de consulta usa `get_reminder_contacts`

**Files:**
- Modify: `scripts/send_appointment_reminders.py:29` (import) e `:162` (chamada)
- Test: `tests/test_reminders.py`

- [ ] **Step 1: Write the failing test**

Adicione a `tests/test_reminders.py` (o módulo já é importado como `rem`):

```python
@pytest.mark.asyncio
async def test_appt_reminder_adult_with_self_only_self():
    # get_reminder_contacts já aplica a regra; o cron só precisa chamá-la.
    client, table = _client()
    with patch("scripts.send_appointment_reminders.get_reminder_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000"}])) as grc, \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new=AsyncMock()) as send:
        await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_consulta", "reminder_day_of_sent_at",
            datetime.now(TZ), None,
        )
    grc.assert_awaited_once_with("p-joao", "consulta", include_inactive=True)
    assert send.await_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reminders.py::test_appt_reminder_adult_with_self_only_self -v`
Expected: FAIL — `AttributeError: ... does not have the attribute 'get_reminder_contacts'` (ainda importa `get_contacts_for_patient`).

- [ ] **Step 3: Write minimal implementation**

Em `scripts/send_appointment_reminders.py` linha 29, troque o import:

```python
from app.patients import get_reminder_contacts
```

Na linha 162, troque a chamada:

```python
    contacts = await get_reminder_contacts(patient_id, "consulta", include_inactive=True) if patient_id else []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: PASS. Se algum teste antigo referenciava `get_contacts_for_patient` no módulo, atualize o alvo do `patch` para `get_reminder_contacts` (mesmo retorno mockado — a assinatura é idêntica).

- [ ] **Step 5: Commit**

```bash
git add scripts/send_appointment_reminders.py tests/test_reminders.py
git commit -m "feat(lembretes): lembrete de consulta só p/ contato próprio de adulto"
```

---

## Task 5: Lembrete de retorno usa `get_reminder_contacts`

**Files:**
- Modify: `scripts/send_return_reminders.py:38` (import) e `:285` (chamada)
- Test: `tests/test_return_reminders_cron.py`

- [ ] **Step 1: Write the failing test**

Confirme primeiro como o teste existente mocka os contatos:

Run: `grep -n "get_contacts_for_patient\|_send_for_row\|patch(" tests/test_return_reminders_cron.py`

Adicione um caso (ajuste o nome do símbolo importado do módulo — abaixo assumo `import scripts.send_return_reminders as ret`; se o arquivo usar outro alias, use o mesmo):

```python
@pytest.mark.asyncio
async def test_return_reminder_adult_with_self_only_self():
    import scripts.send_return_reminders as ret
    row = {
        "id": "rr1", "patient_id": "p-joao",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "patients": {"name": "João Silva"},
    }
    client = MagicMock()
    table = MagicMock()
    for m in ("update", "eq"):
        getattr(table, m).return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João Silva"}])) as grc, \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new=AsyncMock()) as send:
        await ret._send_for_row(client, row, "retorno_no_mes", "month_of_sent_at", None)
    grc.assert_awaited_once_with("p-joao", "consulta", include_inactive=True)
    assert send.await_count == 1
```

(Os imports `pytest`, `MagicMock`, `AsyncMock`, `patch` já devem existir no arquivo; se não, adicione-os no topo.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_return_reminders_cron.py::test_return_reminder_adult_with_self_only_self -v`
Expected: FAIL — `AttributeError` em `get_reminder_contacts` (o módulo ainda importa `get_contacts_for_patient`).

- [ ] **Step 3: Write minimal implementation**

Em `scripts/send_return_reminders.py` linha 38, troque o import:

```python
from app.patients import get_reminder_contacts
```

Na linha 285, troque a chamada:

```python
    contacts = await get_reminder_contacts(patient_id, "consulta", include_inactive=True) if patient_id else []
```

Atualize também o comentário na docstring de `_send_for_row` (linha ~276) que cita `get_contacts_for_patient` para citar `get_reminder_contacts`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: PASS. Atualize qualquer `patch("...get_contacts_for_patient")` remanescente no arquivo para `get_reminder_contacts`.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_return_reminders.py tests/test_return_reminders_cron.py
git commit -m "feat(lembretes): lembrete de retorno só p/ contato próprio de adulto"
```

---

## Task 6: Lembrete/cancelamento de taxa vai só para o contato da reserva

**Files:**
- Modify: `scripts/send_payment_reminders.py` — `_appt_select` (~498), `_send_payment_reminder` (~315), `_cancel_unpaid_appointment` (~354); novo helper `_reminder_recipients`.
- Test: `tests/test_payment_reminders_cancel.py`

**Regra:** o loop de envio (lembrete e cancelamento) usa o contato de `appt["contact_id"]`; se nulo, fallback para `get_financial_contacts`. `find_receipt_in_conversation` e o e-mail à clínica continuam usando **todos** os contatos financeiros.

- [ ] **Step 1: Write the failing test**

Primeiro veja os helpers do arquivo de teste existente:

Run: `grep -n "def \|_appt\|get_financial_contacts\|find_receipt\|send_whatsapp\|_send_payment_reminder\|_cancel_unpaid" tests/test_payment_reminders_cancel.py`

Adicione (ajuste os nomes dos helpers locais se o arquivo já tiver um `_appt`/`_client` — reuse-os em vez de duplicar):

```python
import scripts.send_payment_reminders as pay


def _pay_appt(**kw):
    base = {
        "appointment_id": "evt-1", "start_time": "2026-09-01T14:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "created_at": "2026-08-01T00:00:00+00:00",
        "patient_id": "p-joao", "contact_id": "c-reserva",
        "patients": {"name": "João Silva", "custom_price": 200},
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_payment_reminder_goes_to_booking_contact_only():
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João Silva"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value={"phone": "5581000", "name": "João Silva"})), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()) as sw:
        await pay._send_payment_reminder(client, _pay_appt(), None, datetime.now(TZ))
    assert [c.args[0] for c in sw.await_args_list] == ["5581000"]


@pytest.mark.asyncio
async def test_payment_reminder_fallback_when_no_contact_id():
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()) as sw:
        await pay._send_payment_reminder(client, _pay_appt(contact_id=None), None, datetime.now(TZ))
    assert sorted(c.args[0] for c in sw.await_args_list) == ["5581000", "5581999"]


@pytest.mark.asyncio
async def test_receipt_guard_still_scans_all_financial_contacts():
    # comprovante enviado por um contato que NÃO é o da reserva ainda bloqueia.
    client = MagicMock()
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)) as frc, \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value={"phone": "5581000", "name": "João"})), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()):
        await pay._send_payment_reminder(client, _pay_appt(), None, datetime.now(TZ))
    # a guarda recebeu AMBOS os telefones financeiros, não só o da reserva
    assert sorted(frc.await_args.args[1]) == ["5581000", "5581999"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -k "booking_contact or fallback_when_no_contact or receipt_guard_still" -v`
Expected: FAIL — `test_payment_reminder_goes_to_booking_contact_only` envia para os dois números (código atual usa `financial_contacts`), e `get_contact_by_id` ainda não é importado no módulo.

- [ ] **Step 3: Write minimal implementation**

3a. Import no topo de `scripts/send_payment_reminders.py` (junto dos outros imports de `app`):

```python
from app.patients import get_contact_by_id
```

3b. Adicione `contact_id` ao `_appt_select` (~498):

```python
    _appt_select = (
        "appointment_id, start_time, doctor_id, created_at, payment_reminder_sent_at, "
        "contact_id, patient_id, patients(name, custom_price)"
    )
```

3c. Novo helper (perto de `get_financial_contacts`):

```python
async def _reminder_recipients(appt: dict, financial_contacts: list[dict]) -> list[dict]:
    """Destinatários do lembrete/cancelamento de taxa: só o contato que fez a
    reserva (appointments.contact_id). Fallback para os contatos financeiros
    quando o agendamento não gravou contact_id (linhas antigas/remarcações).

    NÃO usar para a guarda de comprovante nem para o e-mail à clínica — esses
    continuam sobre TODOS os contatos financeiros.
    """
    booking = await get_contact_by_id(appt.get("contact_id"))
    if booking and booking.get("phone"):
        return [{"phone": booking["phone"], "name": booking.get("name")}]
    return financial_contacts
```

3d. Em `_send_payment_reminder`, após a guarda de comprovante (`if _receipt: ... return`), troque o alvo do loop de `financial_contacts` para os destinatários:

```python
    recipients = await _reminder_recipients(appt, financial_contacts)

    any_sent = False
    for contact in recipients:
```

(mantenha `_contact_phones` e a chamada a `find_receipt_in_conversation` como estão — amplos.)

3e. Em `_cancel_unpaid_appointment`, após a guarda de comprovante, troque o loop de notificação de `financial_contacts` para os destinatários:

```python
    recipients = await _reminder_recipients(appt, financial_contacts)

    any_notified = False
    notified_phones = []
    for contact in recipients:
```

(o `_contact_phones` da guarda e a linha `Contatos notificados: {', '.join(c['phone'] for c in financial_contacts)}` do e-mail à clínica permanecem amplos — a clínica vê todos os contatos do paciente.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_payment_reminders_cancel.py -v`
Expected: PASS. Se algum teste antigo esperava envio para todos os contatos no fluxo de reserva/cancelamento, atualize-o: agora o alvo é o contato da reserva (mocke `get_contact_by_id`), enquanto a guarda de comprovante e o e-mail seguem amplos.

- [ ] **Step 5: Commit**

```bash
git add scripts/send_payment_reminders.py tests/test_payment_reminders_cancel.py
git commit -m "feat(lembretes): taxa de reserva só p/ o contato que fez a reserva"
```

---

## Task 7: Suíte completa + verificação final

**Files:** nenhum novo — validação de regressão.

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: todos passam. (Rodar a suíte inteira evita a armadilha de import circular ao passar `test_patients.py` primeiro.)

- [ ] **Step 2: Conferir que nenhum cron ainda importa o helper antigo indevidamente**

Run: `grep -rn "get_contacts_for_patient" scripts/send_appointment_reminders.py scripts/send_return_reminders.py`
Expected: sem resultados (ambos migraram para `get_reminder_contacts`). `get_contacts_for_patient` continua existindo em `app/patients.py` para outros chamadores — não remover.

- [ ] **Step 3: Commit final (se houver ajustes de regressão)**

```bash
git add -A
git commit -m "test(lembretes): ajustes de regressão da suíte completa"
```

---

## Notas de implementação

- **Idade "maior de 18"** = `age >= 18` (adulto), consistente com `age < 18` (menor) usado em todo `app/graph/nodes.py`. `_compute_age` retorna anos completos, então quem faz 18 hoje já conta como adulto.
- **`get_contacts_for_patient` permanece intacta** — só os crons migram para `get_reminder_contacts`. Outros chamadores (ex.: `app/graph/tools.py`) não mudam de comportamento.
- **Guarda de comprovante ampla** é intencional e testada (Task 6, `test_receipt_guard_still_scans_all_financial_contacts`): estreitá-la reintroduz o risco de cancelar consulta já paga por um responsável.
- **Fora de escopo:** `send_pending_payments_reminder.py` (e-mail interno), coleta de `birth_date` antes de agendar, scripts one-off `scripts/_*.py`.
