# Lembretes por idade + contato próprio + quem agendou — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer os lembretes de consulta e retorno irem só para o número certo (adulto → próprio; menor → quem agendou / responsáveis), acertar o marcador is_self/relationship divergente entre roles, e o painel gravar esse marcador em todas as roles.

**Architecture:** Opção A (contida). Mantém o schema de `patient_contacts`. Novas funções puras/de seleção em `app/patients.py` escolhem o destinatário por idade + contato próprio + `appointments.contact_id`. Os crons de consulta e retorno passam a usá-las. Uma migração one-off copia o marcador da linha `agendamento` (íntegra) para `consulta`/`financeiro`. O painel passa a gravar o marcador em todas as roles do par. A cobrança já usa o contato que agendou (nenhuma mudança).

**Tech Stack:** Python 3.14, FastAPI, Supabase (postgrest async), pytest/pytest-asyncio, unittest.mock.

**Contexto de execução:** worktree `.worktrees/lembretes-por-idade-contato`. Rodar tudo de lá. Testes do app: `uv run pytest tests/ --tb=short`. Testes do dashboard: `cd dashboard && uv run pytest`.

---

## Arquivos

- Modificar: `app/patients.py` — novos helpers e funções de seleção.
- Modificar: `tests/test_patients.py` — testes das novas funções.
- Modificar: `scripts/send_appointment_reminders.py` — usar `consultation_reminder_contacts` + SELECT com `contact_id`/`birth_date`.
- Modificar: `tests/test_reminders.py` — ajustar mocks para a nova função.
- Modificar: `scripts/send_return_reminders.py` — usar `return_reminder_contacts`.
- Modificar: `tests/test_return_reminders.py` (ou criar se não existir) — teste do destinatário de retorno.
- Modificar: `dashboard/attendant_db.py` — `update_link` grava marcador em todas as roles do par; `get_link` determinístico (prefere `agendamento`).
- Modificar: `dashboard/tests/test_attendant_db.py` — teste da propagação.
- Criar: `scripts/_migrate_marker_from_agendamento.py` — migração one-off (dry-run + --apply).
- Criar: `tests/test_marker_migration.py` — teste da função pura de decisão.

---

### Task 1: Helpers de relação (`_is_self_like`, `_is_guardian_relationship`)

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/test_patients.py`:

```python
# --- Marker helpers ---
from app.patients import _is_self_like, _is_guardian_relationship


def test_is_self_like():
    assert _is_self_like(None) is True
    assert _is_self_like("") is True
    assert _is_self_like("self") is True
    assert _is_self_like("Próprio") is True
    assert _is_self_like("mãe") is False
    assert _is_self_like("pai") is False


def test_is_guardian_relationship():
    assert _is_guardian_relationship("mãe") is True
    assert _is_guardian_relationship("MAE") is True
    assert _is_guardian_relationship("pai") is True
    assert _is_guardian_relationship("avó") is True
    assert _is_guardian_relationship("avô") is True
    assert _is_guardian_relationship("responsável") is True
    assert _is_guardian_relationship("tutor") is True
    assert _is_guardian_relationship("self") is False
    assert _is_guardian_relationship(None) is False
    assert _is_guardian_relationship("") is False
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_is_guardian_relationship -v`
Expected: FAIL com ImportError (`_is_guardian_relationship`).

- [ ] **Step 3: Implementar em `app/patients.py`** (logo após `_compute_age`)

```python
_SELF_LIKE = {"", "self", "próprio", "proprio", "eu", "mesmo", "a própria", "o próprio"}
_GUARDIAN_RELATIONSHIPS = {
    "mãe", "mae", "pai", "tutor", "tutora", "responsável", "responsavel",
    "responsavel legal", "responsável legal", "avó", "avo", "avô",
    "tio", "tia", "irmã", "irma", "irmão", "irmao", "padrasto", "madrasta",
    "guardião", "guardiao",
}


def _norm_rel(rel: str | None) -> str:
    """Relação normalizada para comparação: sem acento, minúscula, sem espaços extras."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", rel or "")
        if not unicodedata.combining(c)
    )
    return " ".join(stripped.lower().split())


def _is_self_like(rel: str | None) -> bool:
    """True se a relação indica o próprio paciente (self/vazio/None)."""
    if rel is None:
        return True
    return _norm_rel(rel) in {_norm_rel(s) for s in _SELF_LIKE}


def _is_guardian_relationship(rel: str | None) -> bool:
    """True se a relação é de responsável (mãe/pai/tutor/avó/...)."""
    return _norm_rel(rel) in {_norm_rel(s) for s in _GUARDIAN_RELATIONSHIPS}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py::test_is_self_like tests/test_patients.py::test_is_guardian_relationship -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(patients): helpers de relação (self-like / responsável)"
```

---

### Task 2: `_linked_contacts_with_marker`

Coleta os contatos do paciente (todas as roles), deduplicados por contato, com o marcador consolidado. Robusto a divergência residual entre roles: `is_self=True` se qualquer role disser; `relationship` = a de responsável se houver.

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# --- _linked_contacts_with_marker ---
from app.patients import _linked_contacts_with_marker


def _lc_client(pc_rows):
    pc_table = MagicMock()
    pc_table.select.return_value = pc_table
    pc_table.eq.return_value = pc_table
    pc_table.execute = AsyncMock(return_value=MagicMock(data=pc_rows))
    client = MagicMock()
    client.from_.return_value = pc_table
    return client


def _pcm(cid, phone, is_self, relationship, role, active=True):
    return {"contact_id": cid, "is_self": is_self, "relationship": relationship,
            "role": role, "contacts": {"id": cid, "phone": phone, "active": active}}


@pytest.mark.asyncio
async def test_linked_marker_dedupes_and_consolidates():
    # mesma pessoa (c-mae) com is_self divergente entre roles -> is_self True se alguma disser
    rows = [
        _pcm("c-mae", "5581999", False, "mãe", "agendamento"),
        _pcm("c-mae", "5581999", True, "mãe", "consulta"),
        _pcm("c-self", "5581000", True, "self", "consulta"),
    ]
    client = _lc_client(rows)
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await _linked_contacts_with_marker("p1", include_inactive=True)
    by_phone = {o["contact"]["phone"]: o for o in out}
    assert by_phone["5581999"]["is_self"] is True
    assert by_phone["5581999"]["relationship"] == "mãe"
    assert by_phone["5581000"]["is_self"] is True


@pytest.mark.asyncio
async def test_linked_marker_excludes_inactive_by_default():
    rows = [_pcm("c1", "5581000", True, "self", "consulta", active=False)]
    client = _lc_client(rows)
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await _linked_contacts_with_marker("p1", include_inactive=False)
    assert out == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_linked_marker_dedupes_and_consolidates -v`
Expected: FAIL com ImportError.

- [ ] **Step 3: Implementar em `app/patients.py`**

```python
async def _linked_contacts_with_marker(patient_id: str, include_inactive: bool = False) -> list[dict]:
    """Contatos do paciente (todas as roles), deduplicados por contato, com o
    marcador consolidado: [{"contact": <row>, "is_self": bool, "relationship": str|None}].

    Consolidação robusta a divergência residual entre roles: is_self=True se
    qualquer role disser; relationship = a de responsável se houver.
    """
    client = await get_supabase()
    result = (
        await client.from_("patient_contacts")
        .select("contact_id, is_self, relationship, contacts(*)")
        .eq("patient_id", patient_id)
        .execute()
    )
    by_contact: dict[str, dict] = {}
    for row in (result.data or []):
        contact = row.get("contacts")
        if not contact or not (include_inactive or contact.get("active")):
            continue
        cid = contact["id"]
        entry = by_contact.setdefault(
            cid, {"contact": contact, "is_self": False, "relationship": None}
        )
        if row.get("is_self"):
            entry["is_self"] = True
        rel = row.get("relationship")
        if rel and not _is_self_like(rel):
            entry["relationship"] = rel
    return list(by_contact.values())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py::test_linked_marker_dedupes_and_consolidates tests/test_patients.py::test_linked_marker_excludes_inactive_by_default -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(patients): _linked_contacts_with_marker consolidado por contato"
```

---

### Task 3: `consultation_reminder_contacts`

Regra: adulto com contato próprio → só os próprios; senão → o contato que agendou (`appointment.contact_id`); sem booking resolvível → todos os vinculados (fallback).

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# --- consultation_reminder_contacts ---
from app.patients import consultation_reminder_contacts


def _sel_client(pc_rows, birth_date, booking_contact):
    """from_('patient_contacts') -> pc_rows ; from_('patients') -> [{birth_date}] ;
    from_('contacts') -> [booking_contact] (para get_contact_by_id)."""
    def make(data):
        t = MagicMock()
        t.select.return_value = t
        t.eq.return_value = t
        t.execute = AsyncMock(return_value=MagicMock(data=data))
        return t
    pc_t = make(pc_rows)
    pat_t = make([{"birth_date": birth_date}])
    con_t = make([booking_contact] if booking_contact else [])
    client = MagicMock()
    client.from_.side_effect = lambda n: {
        "patient_contacts": pc_t, "patients": pat_t, "contacts": con_t}[n]
    return client


@pytest.mark.asyncio
async def test_consultation_adult_with_self_only_self():
    rows = [_pcm("c-self", "5581000", True, "self", "consulta"),
            _pcm("c-mae", "5581999", True, "mãe", "consulta")]  # mãe corrompida is_self=True
    client = _sel_client(rows, "15/01/1990", {"id": "c-book", "phone": "5581777", "active": True})
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await consultation_reminder_contacts("p1", {"contact_id": "c-book"})
    assert [c["phone"] for c in out] == ["5581000"]


@pytest.mark.asyncio
async def test_consultation_minor_goes_to_booking():
    rows = [_pcm("c-mae", "5581999", False, "mãe", "consulta")]
    client = _sel_client(rows, "12/08/2015", {"id": "c-book", "phone": "5581777", "active": True})
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await consultation_reminder_contacts("p1", {"contact_id": "c-book"})
    assert [c["phone"] for c in out] == ["5581777"]


@pytest.mark.asyncio
async def test_consultation_adult_no_self_falls_back_to_booking():
    rows = [_pcm("c-mae", "5581999", False, "mãe", "consulta")]
    client = _sel_client(rows, "15/01/1990", {"id": "c-book", "phone": "5581777", "active": True})
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await consultation_reminder_contacts("p1", {"contact_id": "c-book"})
    assert [c["phone"] for c in out] == ["5581777"]


@pytest.mark.asyncio
async def test_consultation_no_booking_falls_back_to_all_linked():
    rows = [_pcm("c-mae", "5581999", False, "mãe", "consulta")]
    client = _sel_client(rows, "12/08/2015", None)  # sem booking resolvível
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await consultation_reminder_contacts("p1", {"contact_id": None})
    assert [c["phone"] for c in out] == ["5581999"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_consultation_adult_with_self_only_self -v`
Expected: FAIL com ImportError.

- [ ] **Step 3: Implementar em `app/patients.py`**

```python
async def consultation_reminder_contacts(
    patient_id: str, appointment: dict, include_inactive: bool = True
) -> list[dict]:
    """Destinatários do lembrete de consulta (véspera/dia).

    - Adulto (>=18) com contato próprio (is_self) → só o(s) próprio(s).
    - Caso contrário (menor, ou adulto sem próprio) → o contato que agendou
      (`appointment['contact_id']`).
    - Sem booking resolvível → todos os contatos vinculados (fallback seguro).
    """
    patient = await get_patient_by_id(patient_id)
    age = _compute_age((patient or {}).get("birth_date"))
    linked = await _linked_contacts_with_marker(patient_id, include_inactive=include_inactive)

    own = [l["contact"] for l in linked if l["is_self"] and _is_self_like(l["relationship"])]
    if age is not None and age >= 18 and own:
        return own

    booking = await get_contact_by_id((appointment or {}).get("contact_id"))
    if booking and (include_inactive or booking.get("active")):
        return [booking]

    return [l["contact"] for l in linked]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -k consultation -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(patients): consultation_reminder_contacts por idade/próprio/quem agendou"
```

---

### Task 4: `return_reminder_contacts`

Regra: adulto com próprio → só próprio; senão responsáveis; se não houver responsável mas houver próprio (ex.: menor com telefone dele) → próprio; senão todos os vinculados (fallback degenerado).

**Files:**
- Modify: `app/patients.py`
- Test: `tests/test_patients.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# --- return_reminder_contacts ---
from app.patients import return_reminder_contacts


def _ret_client(pc_rows, birth_date):
    def make(data):
        t = MagicMock()
        t.select.return_value = t
        t.eq.return_value = t
        t.execute = AsyncMock(return_value=MagicMock(data=data))
        return t
    pc_t = make(pc_rows)
    pat_t = make([{"birth_date": birth_date}])
    client = MagicMock()
    client.from_.side_effect = lambda n: {"patient_contacts": pc_t, "patients": pat_t}[n]
    return client


@pytest.mark.asyncio
async def test_return_adult_with_self_only_self():
    rows = [_pcm("c-self", "5581000", True, "self", "consulta"),
            _pcm("c-mae", "5581999", False, "mãe", "consulta")]
    client = _ret_client(rows, "15/01/1990")
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await return_reminder_contacts("p1")
    assert [c["phone"] for c in out] == ["5581000"]


@pytest.mark.asyncio
async def test_return_minor_only_guardians_excludes_third_party():
    rows = [_pcm("c-mae", "5581999", False, "mãe", "consulta"),
            _pcm("c-3p", "5581888", False, None, "consulta")]  # terceiro avulso
    client = _ret_client(rows, "12/08/2015")
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await return_reminder_contacts("p1")
    assert [c["phone"] for c in out] == ["5581999"]


@pytest.mark.asyncio
async def test_return_minor_self_only_falls_back_to_self():
    # menor com telefone próprio e sem responsável (caso Luísa) -> vai pro próprio
    rows = [_pcm("c-self", "5581000", True, "self", "consulta")]
    client = _ret_client(rows, "12/08/2010")
    with _patch("app.patients.get_supabase", new=AsyncMock(return_value=client)):
        out = await return_reminder_contacts("p1")
    assert [c["phone"] for c in out] == ["5581000"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_patients.py::test_return_minor_only_guardians_excludes_third_party -v`
Expected: FAIL com ImportError.

- [ ] **Step 3: Implementar em `app/patients.py`**

```python
async def return_reminder_contacts(
    patient_id: str, include_inactive: bool = True
) -> list[dict]:
    """Destinatários do lembrete de RETORNO.

    - Adulto (>=18) com contato próprio → só o(s) próprio(s).
    - Menor → só os responsáveis (relação mãe/pai/tutor/avó/...), excluindo
      terceiro avulso (is_self=False + relação vazia).
    - Sem responsável mas com próprio (ex.: menor com telefone dele) → o próprio.
    - Senão → todos os vinculados (fallback degenerado, garante entrega).
    """
    patient = await get_patient_by_id(patient_id)
    age = _compute_age((patient or {}).get("birth_date"))
    linked = await _linked_contacts_with_marker(patient_id, include_inactive=include_inactive)

    own = [l["contact"] for l in linked if l["is_self"] and _is_self_like(l["relationship"])]
    if age is not None and age >= 18 and own:
        return own

    guardians = [
        l["contact"] for l in linked
        if _is_guardian_relationship(l["relationship"])
    ]
    if guardians:
        return guardians
    if own:
        return own
    return [l["contact"] for l in linked]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_patients.py -k return_ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "feat(patients): return_reminder_contacts (adulto próprio / menor responsáveis)"
```

---

### Task 5: Cron de consulta usa `consultation_reminder_contacts`

**Files:**
- Modify: `scripts/send_appointment_reminders.py` (imports; SELECTs em `main`; `_send_reminder_to_contacts`)
- Test: `tests/test_reminders.py`

- [ ] **Step 1: Ajustar os testes existentes (que ainda falham após a mudança)**

Em `tests/test_reminders.py`, os testes hoje mockam `get_reminder_contacts`. Trocar por `consultation_reminder_contacts` e ajustar a assinatura esperada. Substituir os três testes que usam `get_reminder_contacts` por:

```python
@pytest.mark.asyncio
async def test_reminder_sent_to_selected_contacts():
    client, table = _client()
    contacts = [{"phone": "5581111"}, {"phone": "5581222"}]
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)
    with patch("scripts.send_appointment_reminders.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock) as mock_send:
        sent = await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    assert set(sent) == {"5581111", "5581222"}
    assert mock_send.await_count == 2
    table.update.assert_called_once()


@pytest.mark.asyncio
async def test_reminder_passes_appointment_and_include_inactive():
    client, table = _client()
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)
    appt = _appt()
    with patch("scripts.send_appointment_reminders.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]) as mock_sel, \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock):
        await rem._send_reminder_to_contacts(
            client, appt, "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    mock_sel.assert_awaited_once_with("p-joao", appt, include_inactive=True)


@pytest.mark.asyncio
async def test_reminder_skips_when_no_contact():
    client, table = _client()
    now = datetime(2026, 6, 19, 7, 0, tzinfo=TZ)
    with patch("scripts.send_appointment_reminders.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[]), \
         patch("scripts.send_appointment_reminders.send_reminder_template",
               new_callable=AsyncMock) as mock_send:
        sent = await rem._send_reminder_to_contacts(
            client, _appt(), "lembrete_dia_anteior",
            "reminder_day_before_sent_at", now, None)
    assert sent == []
    mock_send.assert_not_awaited()
    table.update.assert_not_called()
```

Deixar os demais testes do arquivo que NÃO usam `get_reminder_contacts` como estão.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_reminders.py::test_reminder_passes_appointment_and_include_inactive -v`
Expected: FAIL (import/attribute `consultation_reminder_contacts` não existe no módulo do cron).

- [ ] **Step 3: Implementar a troca no cron**

Em `scripts/send_appointment_reminders.py`:

3a. Trocar o import (linha 28):
```python
from app.patients import consultation_reminder_contacts
```

3b. Em `_send_reminder_to_contacts`, trocar a obtenção de contatos (linhas ~161):
```python
    contacts = await consultation_reminder_contacts(
        patient_id, appt, include_inactive=True
    ) if patient_id else []
    if not contacts:
        print(f"  [SKIP] appt {appointment_id} sem contato para lembrete (patient_id={patient_id})")
        return []
```

3c. Nos dois SELECTs de `main` (day-before ~linha 218 e day-of ~linha 233), incluir `contact_id` e `birth_date`:
```python
            .select("appointment_id, start_time, doctor_id, modality, patient_id, contact_id, patients(name, birth_date)")
```
(aplicar nas duas queries)

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_appointment_reminders.py tests/test_reminders.py
git commit -m "feat(lembrete): consulta usa consultation_reminder_contacts (idade/próprio/quem agendou)"
```

---

### Task 6: Cron de retorno usa `return_reminder_contacts`

**Files:**
- Modify: `scripts/send_return_reminders.py` (import; `_send_for_row`)
- Test: `tests/test_return_reminders.py` (criar se não existir)

- [ ] **Step 1: Escrever o teste que falha**

Verificar primeiro se existe `tests/test_return_reminders.py`:
Run: `ls tests/test_return_reminders.py 2>/dev/null || echo NAO_EXISTE`

Criar/adicionar o teste:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import scripts.send_return_reminders as ret


@pytest.mark.asyncio
async def test_return_row_uses_return_reminder_contacts():
    client = MagicMock()
    row = {"id": "r1", "patient_id": "p1", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
           "patients": {"name": "Fulano"}}
    with patch("scripts.send_return_reminders.return_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581999", "name": "Mãe"}]) as sel, \
         patch("scripts.send_return_reminders._is_stale_classification",
               new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as send, \
         patch.object(client, "from_") as from_:
        upd = MagicMock(); upd.update.return_value = upd; upd.eq.return_value = upd
        upd.execute = AsyncMock(return_value=MagicMock(data=[]))
        from_.return_value = upd
        await ret._send_for_row(client, row, "retorno_no_mes", "month_of_sent_at", None)
    sel.assert_awaited_once_with("p1", include_inactive=True)
    send.assert_awaited_once()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_return_reminders.py::test_return_row_uses_return_reminder_contacts -v`
Expected: FAIL (`return_reminder_contacts` não é atributo do módulo do cron).

- [ ] **Step 3: Implementar a troca no cron**

Em `scripts/send_return_reminders.py`:

3a. Trocar o import (linha 43):
```python
from app.patients import return_reminder_contacts
```

3b. Em `_send_for_row`, trocar a obtenção de contatos (linha ~352):
```python
    contacts = await return_reminder_contacts(patient_id, include_inactive=True) if patient_id else []
    if not contacts:
        print(f"  [SKIP] return_reminder {row.get('id')} sem contato (patient_id={patient_id})")
        return
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_return_reminders.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/send_return_reminders.py tests/test_return_reminders.py
git commit -m "feat(lembrete): retorno usa return_reminder_contacts (adulto próprio / menor responsáveis)"
```

---

### Task 7: Painel grava marcador em todas as roles + leitura determinística

**Files:**
- Modify: `dashboard/attendant_db.py` (`update_link`, `get_link`)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `dashboard/tests/test_attendant_db.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import dashboard.attendant_db as adb


@pytest.mark.asyncio
async def test_update_link_propagates_marker_to_all_roles_of_pair():
    # is_self/relationship devem atualizar todas as linhas do par (patient, contact)
    calls = {}
    client = MagicMock()
    pc = MagicMock()
    def _update(payload):
        calls["payload"] = payload
        pc._where = {}
        return pc
    pc.update.side_effect = _update
    def _eq(col, val):
        pc._where[col] = val
        return pc
    pc.eq.side_effect = _eq
    pc.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = pc
    with patch("dashboard.attendant_db.get_client", new=AsyncMock(return_value=client)), \
         patch("dashboard.attendant_db.get_link_by_id", new=AsyncMock(
             return_value={"id": "pc9", "patient_id": "p1", "contact_id": "c1"})):
        await adb.update_link("pc9", {"is_self": False, "relationship": "mãe"})
    assert calls["payload"] == {"is_self": False, "relationship": "mãe"}
    # filtrou pelo PAR (patient_id + contact_id), não pelo id da linha
    assert pc._where == {"patient_id": "p1", "contact_id": "c1"}


@pytest.mark.asyncio
async def test_update_link_role_field_updates_single_row():
    client = MagicMock()
    pc = MagicMock()
    pc.update.return_value = pc
    where = {}
    pc.eq.side_effect = lambda c, v: (where.__setitem__(c, v), pc)[1]
    pc.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = pc
    with patch("dashboard.attendant_db.get_client", new=AsyncMock(return_value=client)), \
         patch("dashboard.attendant_db.get_link_by_id", new=AsyncMock(
             return_value={"id": "pc9", "patient_id": "p1", "contact_id": "c1"})):
        await adb.update_link("pc9", {"role": "financeiro"})
    assert where == {"id": "pc9"}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py::test_update_link_propagates_marker_to_all_roles_of_pair -v`
Expected: FAIL (hoje filtra por `id`, não pelo par).

- [ ] **Step 3: Implementar em `dashboard/attendant_db.py`**

Substituir `update_link` (linhas 191-196) por:

```python
_MARKER_FIELDS = {"is_self", "relationship"}


async def update_link(pc_id: str, data: dict) -> None:
    """Atualiza um vínculo. `role` afeta só a linha `pc_id`; is_self/relationship
    são propriedade do PAR (paciente, contato) e são gravados em TODAS as roles
    do par — senão as linhas divergem e o cron de lembrete lê um valor e o painel
    mostra outro.
    """
    payload = _filter(data, _LINK_FIELDS)
    if not payload:
        return
    client = await get_client()
    marker = {k: v for k, v in payload.items() if k in _MARKER_FIELDS}
    role_only = {k: v for k, v in payload.items() if k not in _MARKER_FIELDS}

    if marker:
        link = await get_link_by_id(pc_id)
        if link:
            await (
                client.from_("patient_contacts").update(marker)
                .eq("patient_id", link["patient_id"])
                .eq("contact_id", link["contact_id"])
                .execute()
            )
    if role_only:
        await client.from_("patient_contacts").update(role_only).eq("id", pc_id).execute()
```

- [ ] **Step 4: Escrever o teste do `get_link` determinístico**

```python
@pytest.mark.asyncio
async def test_get_link_prefers_agendamento_row():
    rows = [
        {"id": "pc-cons", "role": "consulta", "is_self": True, "relationship": "mãe"},
        {"id": "pc-agen", "role": "agendamento", "is_self": False, "relationship": "mãe"},
    ]
    client = MagicMock()
    t = MagicMock()
    t.select.return_value = t
    t.eq.return_value = t
    t.execute = AsyncMock(return_value=MagicMock(data=rows))
    client.from_.return_value = t
    with patch("dashboard.attendant_db.get_client", new=AsyncMock(return_value=client)):
        link = await adb.get_link("p1", "c1")
    assert link["role"] == "agendamento"
```

- [ ] **Step 5: Implementar `get_link` determinístico**

Substituir o `return rows[0] if rows else None` de `get_link` (linha ~87) por:

```python
    rows = res.data or []
    if not rows:
        return None
    for r in rows:
        if r.get("role") == "agendamento":
            return r
    return rows[0]
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): grava is_self/relationship em todas as roles do par; get_link determinístico"
```

---

### Task 8: Migração one-off (propaga agendamento → consulta/financeiro)

**Files:**
- Create: `scripts/_migrate_marker_from_agendamento.py`
- Test: `tests/test_marker_migration.py`

- [ ] **Step 1: Escrever o teste da função pura de decisão**

Criar `tests/test_marker_migration.py`:

```python
from scripts._migrate_marker_from_agendamento import decide_updates


def _row(role, is_self, rel, pid="p1", cid="c1"):
    return {"patient_id": pid, "contact_id": cid, "role": role,
            "is_self": is_self, "relationship": rel}


def test_propagates_agendamento_to_divergent_roles():
    rows = [_row("agendamento", False, "mãe"),
            _row("consulta", True, "mãe"),
            _row("financeiro", True, "mãe")]
    ups = decide_updates(rows)
    # duas linhas (consulta, financeiro) alinhadas ao agendamento
    assert {(u["role"], u["is_self"], u["relationship"]) for u in ups} == {
        ("consulta", False, "mãe"), ("financeiro", False, "mãe")}


def test_no_update_when_already_consistent():
    rows = [_row("agendamento", True, "self"),
            _row("consulta", True, "self"),
            _row("financeiro", True, "self")]
    assert decide_updates(rows) == []


def test_skips_pair_without_agendamento():
    rows = [_row("consulta", True, "mãe"), _row("financeiro", True, "mãe")]
    assert decide_updates(rows) == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_marker_migration.py -v`
Expected: FAIL (módulo não existe).

- [ ] **Step 3: Implementar `scripts/_migrate_marker_from_agendamento.py`**

```python
"""Migração one-off: copia (is_self, relationship) da linha `agendamento`
(íntegra) para as linhas `consulta`/`financeiro` de cada par (paciente, contato).

Uso:
  uv run python scripts/_migrate_marker_from_agendamento.py         # dry-run
  uv run python scripts/_migrate_marker_from_agendamento.py --apply # aplica
"""
import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

from app.supabase_client import get_supabase


def decide_updates(rows: list[dict]) -> list[dict]:
    """Dado TODAS as linhas patient_contacts de UM par (paciente, contato),
    devolve as linhas de consulta/financeiro que precisam ser alinhadas ao
    agendamento. Vazio se não há agendamento ou já está consistente."""
    ag = next((r for r in rows if r.get("role") == "agendamento"), None)
    if ag is None:
        return []
    target_self = bool(ag.get("is_self"))
    target_rel = ag.get("relationship")
    updates = []
    for r in rows:
        if r.get("role") == "agendamento":
            continue
        if bool(r.get("is_self")) != target_self or r.get("relationship") != target_rel:
            updates.append({
                "patient_id": r["patient_id"], "contact_id": r["contact_id"],
                "role": r["role"], "is_self": target_self, "relationship": target_rel,
            })
    return updates


async def _fetch_all(client):
    rows, start, page = [], 0, 1000
    while True:
        res = await (
            client.from_("patient_contacts")
            .select("patient_id, contact_id, role, is_self, relationship")
            .range(start, start + page - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


async def main(apply: bool):
    client = await get_supabase()
    rows = await _fetch_all(client)
    pairs: dict = {}
    for r in rows:
        pairs.setdefault((r["patient_id"], r["contact_id"]), []).append(r)

    all_updates = []
    for pair_rows in pairs.values():
        all_updates.extend(decide_updates(pair_rows))

    print(f"linhas a alinhar: {len(all_updates)}")
    for u in all_updates:
        print(f"  {u['patient_id']} / {u['contact_id']} role={u['role']} "
              f"-> is_self={u['is_self']} rel={u['relationship']!r}")

    if not apply:
        print("\n(dry-run — nada aplicado; rode com --apply para gravar)")
        return

    for u in all_updates:
        await (
            client.from_("patient_contacts")
            .update({"is_self": u["is_self"], "relationship": u["relationship"]})
            .eq("patient_id", u["patient_id"])
            .eq("contact_id", u["contact_id"])
            .eq("role", u["role"])
            .execute()
        )
    print(f"\naplicado: {len(all_updates)} linhas")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_marker_migration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/_migrate_marker_from_agendamento.py tests/test_marker_migration.py
git commit -m "feat(migração): alinha is_self/relationship das roles à linha de agendamento"
```

---

### Task 9: Verificação final + suíte completa

**Files:** nenhum (verificação)

- [ ] **Step 1: Rodar a suíte do app**

Run: `uv run pytest tests/ --tb=short`
Expected: PASS (sem regressões). Prestar atenção especial em `tests/test_patients.py` (ver nota do CLAUDE.md sobre ordem de import — não rodar `test_patients.py` isolado como 1º argumento junto com test_patients específico).

- [ ] **Step 2: Rodar a suíte do dashboard**

Run: `cd dashboard && uv run pytest --tb=short && cd ..`
Expected: PASS

- [ ] **Step 3: Dry-run da migração e conferência**

Run: `uv run python scripts/_migrate_marker_from_agendamento.py`
Expected: lista de linhas a alinhar (as ~46 fichas divergentes + as 2 já corrigidas na mão convergem para o agendamento). Conferir que nenhuma linha vira PRÓPRIA indevidamente. NÃO aplicar ainda — a aplicação em produção é decisão de rollout com a clínica.

- [ ] **Step 4: Confirmar que `get_reminder_contacts` antiga não é mais usada pelos crons**

Run: `grep -rn "get_reminder_contacts" scripts/ app/`
Expected: só aparece a definição em `app/patients.py` (e testes). Se algum cron ainda referenciar, corrigir. A função antiga pode permanecer no código (não é removida neste plano para não mexer nos testes dela); é candidata a remoção num passo futuro, junto das roles consulta/financeiro.

---

## Self-review (cobertura do spec)

- Lembrete de consulta (adulto próprio / menor quem-agendou / fallback): Tasks 3, 5. ✓
- Lembrete de retorno (adulto próprio / menor responsáveis / fallbacks): Tasks 4, 6. ✓
- Cobrança da taxa → quem agendou: **já implementado** em `send_payment_reminders.py::_reminder_recipients`; verificado na Task 9 (grep) — sem mudança de código. ✓
- Limpeza do marcador (propaga agendamento): Task 8. ✓
- Painel grava em todas as roles + leitura determinística: Task 7. ✓
- Roles consulta/financeiro deixam de escolher destinatário, permanecem na tabela: consequência das Tasks 5/6; nota na Task 9. ✓
- Privacidade da conversa (terceiro não vê outras consultas): **fora de escopo (Projeto 2)**. ✓
