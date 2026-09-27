# Painel da Eva, Parte 3: alterar e cancelar consulta — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A atendente altera (data, hora, duração, médico, modalidade, observação, cobrança, etiqueta 1ª consulta) e cancela consultas pelo painel, com a regra das 24h, reembolso na planilha de Solicitações e mensagem ao paciente.

**Architecture:** Dois endpoints novos na Eva (`POST /admin/panel/appointments/edit` e `/cancel`), implementados num módulo novo `app/panel_appointments.py` que reaproveita `app/panel_booking.py` (checagem de encaixe, criação, envio de mensagem com regra da idade, janela 24h e checkpoint). O painel (`dashboard/`) só valida escopo, repassa para a Eva e desenha as folhas: a folha "Nova consulta" ganha um modo "Alterar" e entra uma folha nova "Cancelar".

**Tech Stack:** FastAPI, postgrest-py async (Supabase), Google Calendar API, LangGraph checkpoint, Jinja2 + JS puro no painel, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-painel-vinculo-e-agendamento-design.md`, seções "Parte 3: editar e cancelar", "Mensagens ao paciente" e "Erros".

---

## Regras do projeto que o executor precisa saber

Trabalhe só dentro da worktree `/Users/ayexatavares/Projetos/psique-ai/.worktrees/painel-editar-cancelar`. Nunca use `git stash` sem tag. Nunca passe `tests/test_patients.py` como primeiro argumento do pytest (import circular). Testes da Eva rodam na raiz da worktree com `uv run pytest ...`; testes do painel rodam dentro de `dashboard/` com `uv run pytest ...`. O painel (`dashboard/`) nunca importa `app/`.

O status de cancelado no banco é `"canceled"` (um L só). Na tabela `appointments`, a coluna `id` é o UUID da linha e `appointment_id` é o id do evento do Google Calendar (texto). Atualize linhas sempre por `id` quando tiver a linha em mãos.

Cortesia vale se `appointments.is_courtesy` for verdadeiro OU `patients.custom_price == 0`. Cortesia e isenção também gravam `booking_fee_waived=True` e `booking_fee_paid_at` (data artificial), então "taxa paga de verdade" é `booking_fee_paid_at` preenchido E cobrança normal.

## Decisões tomadas neste plano (fora do texto literal do spec)

1. Remarcação a pedido do paciente com menos de 24h e taxa paga de verdade: a linha antiga vira `canceled` (a taxa fica retida nela, como registro) e nasce uma linha nova com cobrança normal, pelo mesmo `create_appointments` da Parte 2. É o que a Eva já faz nesse caso (`reschedule_appointment` manda chamar `cancel_appointment` + `confirm_appointment`). Todas as outras alterações ficam na mesma linha.
2. Taxa isenta ou cortesia nunca gera "nova taxa" na remarcação tardia; só taxa paga em dinheiro.
3. No cancelamento, primeiro o banco, depois o Calendar. Se o Calendar falhar, o status volta. Evento já apagado à mão no Calendar (404/410) conta como sucesso.
4. Na 1ª consulta dividida, "cancelar as duas partes" registra a devolução uma vez só (R$ 100,00), na linha que a atendente abriu.
5. O pendente da Parte 2 "`register_payment` ignora `is_courtesy`" entra aqui (Task 10).

## Mapa de arquivos

| Arquivo | O que muda |
|---|---|
| `app/google_calendar.py` | `update_event` aceita `session_note`; `_get_busy` devolve o `id` do evento |
| `app/panel_booking.py` | `check_slot(..., exclude_appointment_id)`; envio e prévia genéricos (`deliver_message`, `preview_message`, `contact_first_name`) |
| `app/booking_texts.py` | `change_text` e `cancel_text` |
| `app/panel_appointments.py` (novo) | validação, alteração, remarcação tardia, cancelamento, reembolso, `handle_edit`, `handle_cancel` |
| `app/main.py` | dois endpoints novos e helper comum de erro |
| `app/graph/tools.py` | `register_payment` respeita `is_courtesy` |
| `dashboard/attendant_db.py` | `list_consultas` devolve `billing` |
| `dashboard/attendant_routes.py` | `_forward_to_eva`, rotas `alterar` e `cancelar` |
| `dashboard/templates/atendente.html` | ícones no cartão, modo Alterar, folha Cancelar |
| Testes | `tests/test_calendar.py`, `tests/test_panel_booking.py`, `tests/test_booking_texts.py`, `tests/test_panel_appointments.py` (novo), `tests/test_webhook.py`, `tests/test_tools.py`, `dashboard/tests/test_attendant_db.py`, `dashboard/tests/test_attendant_routes.py`, `dashboard/tests/test_attendant_scope.py` |

---

### Task 1: Calendar: observação na edição e id no busy

**Files:**
- Modify: `app/google_calendar.py` (`update_event` ~linha 991, `_get_busy` ~linha 577)
- Test: `tests/test_calendar.py` (acrescentar no fim)

- [ ] **Step 1: Write the failing tests**

Acrescente ao fim de `tests/test_calendar.py`:

```python
# ── update_event com observação / _get_busy com id (Painel Parte 3) ─────────

async def test_update_event_session_note_goes_to_summary_and_description():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app import google_calendar as gc
    start = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/Recife"))
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=MagicMock()), \
         patch("app.google_calendar._update_event") as mock_upd:
        await gc.update_event(
            calendar_id="cal", event_id="evt1", new_start=start, slot_minutes=60,
            patient_name="Lucas", doctor_name="Dr. Júlio", modality="presencial",
            session_note="Domiciliar",
        )
    body = mock_upd.call_args[0][3]
    assert body["summary"] == "Consulta — Lucas [Presencial] (Domiciliar)"
    assert body["description"].endswith("\n\nDomiciliar")
    assert "1ª hora" not in body["description"]


async def test_update_event_without_note_keeps_old_summary():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app import google_calendar as gc
    start = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/Recife"))
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=MagicMock()), \
         patch("app.google_calendar._update_event") as mock_upd:
        await gc.update_event(
            calendar_id="cal", event_id="evt1", new_start=start, slot_minutes=60,
            patient_name="Lucas", doctor_name="Dr. Júlio", modality="online",
        )
    assert mock_upd.call_args[0][3]["summary"] == "Consulta — Lucas [Online]"


def test_get_busy_includes_event_id():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.google_calendar import _get_busy
    tz = ZoneInfo("America/Recife")
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": [{
        "id": "evt9", "summary": "Consulta — Ana [Online]",
        "start": {"dateTime": "2026-10-05T09:00:00-03:00"},
        "end": {"dateTime": "2026-10-05T10:00:00-03:00"},
    }]}
    busy = _get_busy(service, "cal", datetime(2026, 10, 5, 8, tzinfo=tz), datetime(2026, 10, 5, 12, tzinfo=tz))
    assert busy == [{"id": "evt9", "start": "2026-10-05T09:00:00-03:00", "end": "2026-10-05T10:00:00-03:00"}]
```

Confira no topo do arquivo que `patch` e `MagicMock` já são importados de `unittest.mock` e que o arquivo usa `pytest-asyncio` em modo auto (as outras funções `async def test_` do arquivo não têm decorator). Se o arquivo usar `@pytest.mark.asyncio`, ponha o decorator nos dois testes async.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_calendar.py -q -k "update_event or get_busy_includes"`
Expected: FAIL (`update_event() got an unexpected keyword argument 'session_note'` e o dict sem `id`).

- [ ] **Step 3: Implement**

Em `app/google_calendar.py`, `update_event` ganha o parâmetro `session_note: str = ""` (depois de `patient_number`) e passa a montar descrição e título como `create_event` faz. Troque o trecho da descrição e do título por:

```python
    if patient_email:
        description += f"\nE-mail: {patient_email}"
    if session_note:
        description += f"\n\n{session_note}"
    elif is_minor_first:
        description += "\n\n1ª hora: conversa com os pais/responsáveis\n2ª hora: consulta com o paciente"

    modality_label = "Online" if modality == "online" else "Presencial"
    new_summary = f"Consulta — {patient_name} [{modality_label}]"
    if session_note:
        new_summary += f" ({session_note})"
```

Em `_get_busy`, troque o `append` por:

```python
        busy.append({"id": evt.get("id"), "start": start_raw["dateTime"], "end": end_raw["dateTime"]})
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_calendar.py tests/test_tools.py -q`
Expected: PASS (nenhum teste antigo compara o dict do busy inteiro; se algum comparar, acrescente `"id"` na expectativa dele).

- [ ] **Step 5: Commit**

```bash
git add app/google_calendar.py tests/test_calendar.py
git commit -m "feat(calendar): update_event aceita observação e busy traz o id do evento"
```

---

### Task 2: `check_slot` ignora a própria consulta em edição

**Files:**
- Modify: `app/panel_booking.py` (`_calendar_busy` e `check_slot`)
- Test: `tests/test_panel_booking.py`

- [ ] **Step 1: Write the failing tests**

Acrescente depois de `test_check_slot_accepts_utc_datetime_converts_to_recife`:

```python
@pytest.mark.asyncio
async def test_check_slot_excludes_appointment_being_edited():
    client, q = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock,
               return_value=[{"id": "evt-self", "start": "2026-10-05T09:00:00-03:00"}]):
        reasons = await pb.check_slot("julio", MON_9, 60, patient_id="p1", exclude_appointment_id="evt-self")
    assert reasons == []
    q.neq.assert_called_with("appointment_id", "evt-self")


@pytest.mark.asyncio
async def test_check_slot_without_exclusion_does_not_call_neq():
    client, q = _sb([])
    with patch("app.panel_booking.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.panel_booking._calendar_busy", new_callable=AsyncMock, return_value=[]):
        await pb.check_slot("julio", MON_9, 60, patient_id="p1")
    q.neq.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_panel_booking.py -q -k check_slot`
Expected: FAIL (`unexpected keyword argument 'exclude_appointment_id'`).

- [ ] **Step 3: Implement**

Em `check_slot`, mude a assinatura e a consulta. A docstring ganha uma frase: "`exclude_appointment_id`: na edição, a própria consulta não conta como conflito (nem no banco nem no Calendar)."

```python
async def check_slot(doctor: str, start: datetime, minutes: int, patient_id: str,
                     exclude_appointment_id: str | None = None) -> list[str]:
    ...
    client = await get_supabase()
    query = (
        client.from_("appointments")
        .select("patient_id, start_time, patients(name)")
        .eq("doctor_id", DOCTOR_IDS[doctor])
        .eq("status", "scheduled")
        .lt("start_time", end.isoformat())
        .gt("end_time", start.isoformat())
    )
    if exclude_appointment_id:
        query = query.neq("appointment_id", exclude_appointment_id)
    res = await query.execute()
    ...
    for ev in await _calendar_busy(doctor, start, end):
        if exclude_appointment_id and ev.get("id") == exclude_appointment_id:
            continue
        hhmm = _to_local(ev["start"]).strftime("%H:%M")
        ...
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "feat(painel): check_slot ignora a consulta em edição"
```

---

### Task 3: envio e prévia de mensagem genéricos

Hoje `send_booking_message` e `message_preview` só sabem montar o texto de confirmação. Alterar e cancelar precisam do mesmo envio (regra da idade, `manual_hold`, janela de 24h, `messages`, checkpoint) com outro texto. Extraia o miolo para funções que recebem `text_for(contact) -> str`.

**Files:**
- Modify: `app/panel_booking.py`
- Test: `tests/test_panel_booking.py`

- [ ] **Step 1: Write the failing tests**

Acrescente depois dos testes de `send_booking_message`:

```python
@pytest.mark.asyncio
async def test_deliver_message_uses_text_for_per_contact():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "CARLA MENEZES"}]
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(return_value=MagicMock(values={"stage": "patient_agent"}))
    chatbot.aupdate_state = AsyncMock()
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]), \
         patch("app.panel_booking._window_open", new_callable=AsyncMock, return_value=True), \
         patch("app.panel_booking.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.panel_booking.save_message", new_callable=AsyncMock), \
         patch("app.graph.graph.chatbot", chatbot):
        out = await pb.deliver_message("p1", "c1", lambda c: f"oi {pb.contact_first_name(c)}", "Lucas", "julio")
    assert mock_send.call_args[0][1] == "oi Carla"
    assert out == {"sent": ["CARLA MENEZES"], "not_delivered": [], "held": []}


@pytest.mark.asyncio
async def test_preview_message_uses_first_recipient():
    recips = [{"id": "c1", "phone": "5581999998888", "name": "Carla"}]
    with patch("app.panel_booking.consultation_reminder_contacts", new_callable=AsyncMock, return_value=recips), \
         patch("app.panel_booking._linked_contacts_with_marker", new_callable=AsyncMock, return_value=[]):
        out = await pb.preview_message("p1", "c1", lambda c: f"texto para {c['name']}")
    assert out["text"] == "texto para Carla"
    assert out["recipients"] == [{"name": "Carla", "phone_hint": "8888"}]


def test_contact_first_name_empty():
    assert pb.contact_first_name({}) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_panel_booking.py -q -k "deliver_message or preview_message or contact_first_name"`
Expected: FAIL (`AttributeError: ... has no attribute 'deliver_message'`).

- [ ] **Step 3: Implement**

Em `app/panel_booking.py`:

1. Acrescente, antes de `_text_for`:

```python
def contact_first_name(contact: dict) -> str:
    """Nome curto do contato para a saudação ('CARLA MENEZES' -> 'Carla'); '' se não houver."""
    return display_name(_nice(contact.get("name") or ""))
```

2. `_text_for` passa a usar: `name = contact_first_name(contact) or "tudo bem"` (mesmo resultado de hoje).

3. Troque `message_preview` por um par genérico + wrapper:

```python
async def preview_message(patient_id: str, contact_id: str, text_for) -> dict:
    """Prévia: texto como o 1º destinatário veria, destinatários e quem está com a Eva desligada."""
    recips, held = await _recipients(patient_id, contact_id)
    first = recips[0] if recips else {}
    return {
        "text": text_for(first),
        "recipients": [{"name": c.get("name") or "", "phone_hint": (c.get("phone") or "")[-4:]} for c in recips],
        "held": held,
    }


async def message_preview(patient_id: str, contact_id: str, kind: str, lines: list[str],
                          pending_part2: bool) -> dict:
    return await preview_message(patient_id, contact_id,
                                 lambda c: _text_for(c, kind, lines, pending_part2))
```

4. Renomeie o corpo de `send_booking_message` para `deliver_message(patient_id, contact_id, text_for, patient_name, doctor)`, trocando a linha `text = _text_for(c, kind, lines, pending_part2)` por `text = text_for(c)`. A docstring atual vai junto. Depois recrie o wrapper:

```python
async def send_booking_message(patient_id: str, contact_id: str, kind: str, lines: list[str],
                               pending_part2: bool, patient_name: str, doctor: str) -> dict:
    return await deliver_message(patient_id, contact_id,
                                 lambda c: _text_for(c, kind, lines, pending_part2),
                                 patient_name, doctor)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_panel_booking.py tests/test_webhook.py -q`
Expected: PASS (os testes antigos de `send_booking_message` e `message_preview` continuam iguais).

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "refactor(painel): envio e prévia de mensagem aceitam qualquer texto"
```

---

### Task 4: textos de alteração e cancelamento

**Files:**
- Modify: `app/booking_texts.py`
- Test: `tests/test_booking_texts.py`

- [ ] **Step 1: Write the failing tests**

Acrescente ao fim de `tests/test_booking_texts.py`:

```python
from app.booking_texts import cancel_text, change_text
from app.graph.prompts import CORRECT_PIX_KEY


def test_change_text_clinic():
    t = change_text("clinic", "Carla", "linha velha", "linha nova")
    assert t.startswith("Olá, Carla! A Clínica Psiquê precisou alterar sua consulta.")
    assert "Era: linha velha\nAgora: linha nova" in t
    assert "taxa" not in t


def test_change_text_patient_with_new_fee():
    t = change_text("patient", "", "a", "b", new_fee=True)
    assert t.startswith("Olá! Conforme combinado, sua consulta foi alterada.")
    assert "menos de 24h" in t and "R$ 100,00" in t and CORRECT_PIX_KEY in t


def test_cancel_text_refund_and_plural():
    t = cancel_text("clinic", "Carla", ["l1", "l2"], "devolver")
    assert t.startswith("Olá, Carla! A Clínica Psiquê precisou cancelar suas consultas:\nl1\nl2")
    assert "devolução da taxa" in t


def test_cancel_text_credit():
    t = cancel_text("patient", "Carla", ["l1"], "credito")
    assert "Conforme combinado, sua consulta foi cancelada:\nl1" in t
    assert "fica guardado para a próxima consulta" in t


def test_cancel_text_no_fee_mention_when_retained_or_none():
    for action in ("reter", None):
        t = cancel_text("patient", "Carla", ["l1"], action)
        assert "taxa" not in t
        assert t.endswith("Se quiser marcar uma nova data, é só responder aqui.")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_booking_texts.py -q`
Expected: FAIL (`ImportError: cannot import name 'cancel_text'`).

- [ ] **Step 3: Implement**

Acrescente ao fim de `app/booking_texts.py`:

```python
def _hello(contact_name: str) -> str:
    return f"Olá, {contact_name}!" if contact_name else "Olá!"


def change_text(initiated_by: str, contact_name: str, old_line: str, new_line: str,
                new_fee: bool = False) -> str:
    """Aviso de consulta alterada pelo painel. initiated_by: patient | clinic."""
    opening = ("A Clínica Psiquê precisou alterar sua consulta." if initiated_by == "clinic"
               else "Conforme combinado, sua consulta foi alterada.")
    txt = f"{_hello(contact_name)} {opening}\n\nEra: {old_line}\nAgora: {new_line}"
    if new_fee:
        return txt + (
            "\n\nComo a mudança foi pedida com menos de 24h de antecedência, a taxa de reserva "
            "anterior não é reaproveitada. Para garantir a nova data, é necessário o pagamento de "
            f"uma nova taxa de R$ 100,00 em até 2 horas.\n💳 PIX: {CORRECT_PIX_KEY}"
        )
    return txt + "\n\nQualquer dúvida, é só responder aqui."


_CANCEL_FEE_LINES = {
    "devolver": "A equipe vai providenciar a devolução da taxa de reserva.",
    "credito": "O valor da taxa de reserva fica guardado para a próxima consulta. "
               "Quando quiser remarcar, é só responder aqui.",
}


def cancel_text(initiated_by: str, contact_name: str, lines: list[str], fee_action: str | None) -> str:
    """Aviso de consulta cancelada pelo painel. fee_action: devolver | credito | reter | None."""
    plural = len(lines) > 1
    if initiated_by == "clinic":
        opening = "A Clínica Psiquê precisou cancelar " + ("suas consultas:" if plural else "sua consulta:")
    else:
        opening = "Conforme combinado, " + ("suas consultas foram canceladas:" if plural else "sua consulta foi cancelada:")
    txt = f"{_hello(contact_name)} {opening}\n" + "\n".join(lines)
    fee = _CANCEL_FEE_LINES.get(fee_action or "")
    return txt + (f"\n\n{fee}" if fee else "\n\nSe quiser marcar uma nova data, é só responder aqui.")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_booking_texts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/booking_texts.py tests/test_booking_texts.py
git commit -m "feat(painel): textos de consulta alterada e cancelada"
```

---

### Task 5: `panel_appointments`: carga, cobrança e validação da alteração

**Files:**
- Create: `app/panel_appointments.py`
- Create: `tests/test_panel_appointments.py`

- [ ] **Step 1: Write the failing tests**

Crie `tests/test_panel_appointments.py`:

```python
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import panel_appointments as pa
from app.panel_booking import BookingError, PanelInputError

TZ = ZoneInfo("America/Recife")
JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
BRUNA = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
NOW = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)

ROW = {
    "id": "uuid-1", "appointment_id": "evt1", "patient_id": "p1", "contact_id": "c1",
    "doctor_id": JULIO, "status": "scheduled", "modality": "presencial",
    "start_time": "2026-10-05T12:00:00+00:00", "end_time": "2026-10-05T13:00:00+00:00",
    "consultation_type": None, "session_note": None, "booking_fee_paid_at": None,
    "booking_fee_waived": False, "is_courtesy": False, "refund_requested_at": None,
}
ADULT = {"id": "p1", "name": "Ana Souza", "birth_date": "01/01/1990", "custom_price": None,
         "modality_restriction": None, "social_name": None, "email": "ana@x.com", "patient_cpf": "123"}
KID = {**ADULT, "name": "Lucas Menezes", "birth_date": "01/01/2018"}
EDIT = {"phone": "5581999998888", "appointment_id": "evt1", "doctor": "julio", "modality": "presencial",
        "start": "2026-10-06T09:00", "minutes": 60, "session_note": "", "first_consultation": False,
        "billing": "normal", "initiated_by": "clinic", "agent": "Maria"}


def load_patches(row=None, patient=None, linked_ids=("c1",), sibling_rows=()):
    """Tudo que _load precisa, com relógio fixo em NOW."""
    return [
        patch("app.panel_appointments._now", return_value=NOW),
        patch("app.panel_appointments.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "c1"}),
        patch("app.panel_appointments._fetch_row", new_callable=AsyncMock, return_value=row if row is not None else ROW),
        patch("app.panel_appointments._linked_contacts_with_marker", new_callable=AsyncMock,
              return_value=[{"contact": {"id": cid}} for cid in linked_ids]),
        patch("app.panel_appointments.get_patient_by_id", new_callable=AsyncMock, return_value=patient or ADULT),
        patch("app.panel_appointments._fetch_active_rows", new_callable=AsyncMock, return_value=list(sibling_rows)),
    ]


async def build_edit(body=None, **kw):
    with ExitStack() as st:
        for p in load_patches(**kw):
            st.enter_context(p)
        return await pa.build_edit({**EDIT, **(body or {})})


# ── cobrança ────────────────────────────────────────────────────────────────

def test_current_billing():
    assert pa.current_billing(ROW, ADULT) == "normal"
    assert pa.current_billing({**ROW, "booking_fee_waived": True}, ADULT) == "taxa_isenta"
    assert pa.current_billing({**ROW, "is_courtesy": True, "booking_fee_waived": True}, ADULT) == "cortesia"
    assert pa.current_billing(ROW, {**ADULT, "custom_price": 0}) == "cortesia"


def test_fee_really_paid_ignores_waiver_timestamp():
    assert pa.fee_really_paid({**ROW, "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}, ADULT)
    assert not pa.fee_really_paid({**ROW, "booking_fee_paid_at": "x", "booking_fee_waived": True}, ADULT)


def test_billing_update_back_to_normal_clears_artificial_paid_at():
    row = {**ROW, "booking_fee_waived": True, "booking_fee_paid_at": "x"}
    assert pa.billing_update(row, ADULT, "normal", "now") == {
        "is_courtesy": False, "booking_fee_waived": False, "booking_fee_paid_at": None}


def test_billing_update_to_courtesy_keeps_real_payment_date():
    row = {**ROW, "booking_fee_paid_at": "paid"}
    assert pa.billing_update(row, ADULT, "cortesia", "now") == {
        "is_courtesy": True, "booking_fee_waived": True, "booking_fee_paid_at": "paid"}


def test_billing_update_same_billing_is_empty():
    assert pa.billing_update(ROW, ADULT, "normal", "now") == {}


# ── _load / build_edit ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_edit_requires_initiated_by():
    with pytest.raises(PanelInputError, match="quem pediu"):
        await build_edit({"initiated_by": None})


@pytest.mark.asyncio
async def test_build_edit_rejects_appointment_of_other_contact():
    with pytest.raises(PanelInputError, match="não pertence"):
        await build_edit(linked_ids=("c9",))


@pytest.mark.asyncio
async def test_build_edit_rejects_canceled_appointment():
    with pytest.raises(PanelInputError, match="não encontrada"):
        await build_edit(row={**ROW, "status": "canceled"})


@pytest.mark.asyncio
async def test_build_edit_nothing_changed():
    with pytest.raises(PanelInputError, match="nada mudou"):
        await build_edit({"start": "2026-10-05T09:00"})


@pytest.mark.asyncio
async def test_build_edit_new_time_in_past():
    with pytest.raises(PanelInputError, match="já passou"):
        await build_edit({"start": "2026-09-30T09:00"})


@pytest.mark.asyncio
async def test_build_edit_40min_only_bruna():
    with pytest.raises(PanelInputError, match="duração"):
        await build_edit({"minutes": 40})


@pytest.mark.asyncio
async def test_build_edit_time_change_notifies():
    req = await build_edit()
    assert req["changes"] == {"start"}
    assert req["notify"] is True and req["late_fee"] is False
    assert req["new"]["start"] == datetime(2026, 10, 6, 9, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_build_edit_note_only_does_not_notify():
    req = await build_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"})
    assert req["changes"] == {"note"} and req["notify"] is False


@pytest.mark.asyncio
async def test_build_edit_keeps_split_label_and_forces_first():
    row = {**ROW, "session_note": "1ª consulta · parte 1 de 2", "consultation_type": "primeira_consulta"}
    req = await build_edit({"session_note": "Domiciliar"}, row=row, patient=KID)
    assert req["new"]["note"] == "1ª consulta · parte 1 de 2 · Domiciliar"
    assert req["new"]["ctype"] == "primeira_consulta"


@pytest.mark.asyncio
async def test_build_edit_split_part_cannot_change_doctor():
    row = {**ROW, "session_note": "1ª consulta · parte 2 de 2"}
    with pytest.raises(PanelInputError, match="dividida"):
        await build_edit({"doctor": "bruna"}, row=row, patient=KID)


@pytest.mark.asyncio
async def test_build_edit_minor_julio_first_flag():
    req = await build_edit({"start": "2026-10-05T09:00", "first_consultation": True}, patient=KID)
    assert req["new"]["ctype"] == "primeira_consulta" and req["changes"] == {"consultation_type"}


@pytest.mark.asyncio
async def test_build_edit_late_fee_patient_under_24h_with_paid_fee():
    row = {**ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00",
           "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}
    req = await build_edit({"initiated_by": "patient"}, row=row)
    assert req["late_fee"] is True


@pytest.mark.asyncio
async def test_build_edit_no_late_fee_when_clinic_or_waived():
    row = {**ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00",
           "booking_fee_paid_at": "x"}
    assert (await build_edit({"initiated_by": "clinic"}, row=row))["late_fee"] is False
    waived = {**row, "booking_fee_waived": True}
    assert (await build_edit({"initiated_by": "patient", "billing": "taxa_isenta"}, row=waived))["late_fee"] is False


@pytest.mark.asyncio
async def test_build_edit_modality_restriction():
    with pytest.raises(PanelInputError, match="só pode ser atendido"):
        await build_edit({"modality": "online"}, patient={**ADULT, "modality_restriction": "presencial"})


@pytest.mark.asyncio
async def test_build_edit_custom_price_zero_stays_courtesy():
    row = {**ROW, "is_courtesy": True, "booking_fee_waived": True, "booking_fee_paid_at": "x"}
    req = await build_edit({"billing": "normal"}, row=row, patient={**ADULT, "custom_price": 0})
    assert req["new"]["billing"] == "cortesia" and "billing" not in req["changes"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_panel_appointments.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.panel_appointments'`).

- [ ] **Step 3: Implement**

Crie `app/panel_appointments.py`:

```python
"""Alterar e cancelar consulta pelo painel da atendente.

Endpoints /admin/panel/appointments/edit e /cancel (app/main.py). Mesmas regras
da Eva: remarcação a pedido do paciente com menos de 24h não reaproveita a taxa
paga (reschedule_appointment), cancelamento com crédito vira pending_reschedule
(cancel_appointment com preserve_fee), devolução vai para a planilha de
Solicitações (register_refund_request). A mensagem ao paciente sai por
panel_booking.deliver_message: regra da idade, manual_hold, janela de 24h,
`messages` e checkpoint.
"""
import logging
from datetime import datetime, timedelta

from app.booking_texts import DOCTOR_LABELS, cancel_text, change_text, format_appt_line
from app.database import DOCTOR_IDS, get_supabase, log_event
from app.panel_booking import (
    SPLIT_PART1,
    SPLIT_PART2,
    BookingError,
    PanelInputError,
    _age_on,
    _is_minor_julio,
    _notify_clinic_async,
    _now,
    _parse_start,
    _thread,
    _to_local,
    check_slot,
    contact_first_name,
    create_appointments,
    deliver_message,
    preview_message,
)
from app.patients import _linked_contacts_with_marker, get_contact_by_phone, get_patient_by_id

_logger = logging.getLogger(__name__)

_DOCTOR_BY_ID = {v: k for k, v in DOCTOR_IDS.items()}
_ACTIVE = ("scheduled", "pending_reschedule")
_SLOT_FIELDS = {"start", "minutes", "doctor"}
_NOTIFY_FIELDS = {"start", "minutes", "doctor", "modality"}
BOOKING_FEE = "100,00"
_APPT_COLS = (
    "id, appointment_id, patient_id, contact_id, doctor_id, start_time, end_time, status, "
    "modality, consultation_type, session_note, booking_fee_paid_at, booking_fee_waived, "
    "is_courtesy, refund_requested_at"
)


# ── banco (funções pequenas para os testes trocarem) ────────────────────────

async def _fetch_row(appointment_id: str) -> dict | None:
    client = await get_supabase()
    res = await (client.from_("appointments").select(_APPT_COLS)
                 .eq("appointment_id", appointment_id).limit(1).execute())
    return (res.data or [None])[0]


async def _fetch_active_rows(patient_id: str) -> list[dict]:
    client = await get_supabase()
    res = await (client.from_("appointments").select(_APPT_COLS)
                 .eq("patient_id", patient_id).in_("status", list(_ACTIVE)).execute())
    return res.data or []


async def _update_row(row_id: str, fields: dict) -> None:
    """Atualiza pela coluna `id` (UUID da linha), nunca pelo id do Calendar."""
    client = await get_supabase()
    await client.from_("appointments").update(fields).eq("id", row_id).execute()


async def _delete_row(appointment_id: str) -> None:
    client = await get_supabase()
    await client.from_("appointments").delete().eq("appointment_id", appointment_id).execute()


# ── cobrança ───────────────────────────────────────────────────────────────

def current_billing(row: dict, patient: dict) -> str:
    if row.get("is_courtesy") or patient.get("custom_price") == 0:
        return "cortesia"
    if row.get("booking_fee_waived"):
        return "taxa_isenta"
    return "normal"


def fee_really_paid(row: dict, patient: dict) -> bool:
    """Taxa paga em dinheiro. Isenção e cortesia também gravam booking_fee_paid_at
    (data artificial), por isso a cobrança precisa ser a normal."""
    return bool(row.get("booking_fee_paid_at")) and current_billing(row, patient) == "normal"


def billing_update(row: dict, patient: dict, billing: str, now_iso: str) -> dict:
    """Campos de taxa para trocar a cobrança de uma consulta existente. {} se não mudou."""
    if patient.get("custom_price") == 0:
        billing = "cortesia"
    if billing == current_billing(row, patient):
        return {}
    if billing in ("cortesia", "taxa_isenta"):
        return {"is_courtesy": billing == "cortesia", "booking_fee_waived": True,
                "booking_fee_paid_at": row.get("booking_fee_paid_at") or now_iso}
    # Volta ao normal: a data de isenção/cortesia era artificial, a taxa volta a ser devida.
    return {"is_courtesy": False, "booking_fee_waived": False, "booking_fee_paid_at": None}


# ── apoio ──────────────────────────────────────────────────────────────────

def _split_label(note: str | None) -> str:
    note = note or ""
    for label in (SPLIT_PART1, SPLIT_PART2):
        if note.startswith(label):
            return label
    return ""


def _display(patient: dict) -> str:
    name = patient.get("name") or ""
    return f"{name} ({patient['social_name']})" if patient.get("social_name") else name


def _dur(minutes: int) -> str:
    return {60: "1h", 120: "2h"}.get(minutes, f"{minutes}min")


def _line(doctor: str, start: datetime, note: str, modality: str, minutes: int) -> str:
    """Linha da mensagem de alteração: médico, data, observação, modalidade e duração,
    para a mudança aparecer mesmo quando só a modalidade ou a duração mudou."""
    mod = "Online" if modality == "online" else "Presencial"
    return f"{format_appt_line(doctor, start, note)} · {mod} · {_dur(minutes)}"


async def _load(body: dict) -> dict:
    """Consulta, paciente e contato da conversa, com as guardas comuns a alterar e cancelar."""
    phone = _thread(body.get("phone") or "")
    contact = await get_contact_by_phone(phone)
    if not contact:
        raise PanelInputError("contato não encontrado")
    row = await _fetch_row(body.get("appointment_id") or "")
    if not row or row.get("status") not in _ACTIVE or not row.get("patient_id"):
        raise PanelInputError("consulta não encontrada ou já encerrada")
    linked = await _linked_contacts_with_marker(row["patient_id"], include_inactive=True)
    if contact["id"] not in {lc["contact"]["id"] for lc in linked}:
        raise PanelInputError("esta consulta não pertence a este contato")
    patient = await get_patient_by_id(row["patient_id"])
    if not patient:
        raise PanelInputError("paciente não encontrado")
    initiated_by = body.get("initiated_by")
    if initiated_by not in (None, "patient", "clinic"):
        raise PanelInputError("quem pediu: paciente ou clínica")
    start, end = _to_local(row["start_time"]), _to_local(row["end_time"])
    return {
        "phone": phone, "contact": contact, "row": row, "patient": patient,
        "doctor": _DOCTOR_BY_ID.get(row["doctor_id"], ""), "start": start,
        "minutes": int((end - start).total_seconds() // 60),
        "initiated_by": initiated_by, "agent": (body.get("agent") or "").strip()[:80],
        # Quem recebe: a regra da idade parte do contato que agendou; sem ele, o da conversa.
        "recipient_contact_id": row.get("contact_id") or contact["id"],
    }


async def build_edit(body: dict) -> dict:
    """Valida a alteração e calcula o que mudou."""
    ctx = await _load(body)
    if ctx["initiated_by"] is None:
        raise PanelInputError("informe quem pediu a mudança: paciente ou clínica")
    row, patient = ctx["row"], ctx["patient"]

    doctor = body.get("doctor")
    if doctor not in DOCTOR_IDS:
        raise PanelInputError("médico inválido")
    modality = body.get("modality")
    if modality not in ("online", "presencial"):
        raise PanelInputError("modalidade inválida")
    restriction = patient.get("modality_restriction")
    if restriction in ("online", "presencial") and modality != restriction:
        raise PanelInputError(f"este paciente só pode ser atendido {restriction}")
    billing = body.get("billing", "normal")
    if billing not in ("normal", "taxa_isenta", "cortesia"):
        raise PanelInputError("cobrança inválida")
    try:
        minutes = int(body.get("minutes") or 0)
    except (TypeError, ValueError):
        raise PanelInputError("duração inválida")
    if minutes not in (40, 60, 120) or (minutes == 40 and doctor != "bruna"):
        raise PanelInputError("duração inválida")
    start = _parse_start(body.get("start"))
    if start != ctx["start"] and start < _now():
        raise PanelInputError("esse horário já passou")

    label = _split_label(row.get("session_note"))
    if label and (doctor != "julio" or minutes != 60):
        raise PanelInputError("parte da 1ª consulta dividida: 1h com o Dr. Júlio")
    extra = (body.get("session_note") or "").strip()[:80]
    note = " · ".join(x for x in (label, extra) if x)
    if label:
        ctype = "primeira_consulta"
    elif _is_minor_julio(patient, doctor, start.date()):
        ctype = "primeira_consulta" if body.get("first_consultation") else "acompanhamento"
    else:
        ctype = None
    if patient.get("custom_price") == 0:
        billing = "cortesia"

    compare = {
        "start": (start, ctx["start"]), "minutes": (minutes, ctx["minutes"]),
        "doctor": (doctor, ctx["doctor"]), "modality": (modality, row.get("modality")),
        "note": (note, row.get("session_note") or ""),
        "consultation_type": (ctype, row.get("consultation_type")),
        "billing": (billing, current_billing(row, patient)),
    }
    changes = {k for k, (new, old) in compare.items() if new != old}
    if not changes and row["status"] == "scheduled":
        raise PanelInputError("nada mudou")

    late_fee = (
        ctx["initiated_by"] == "patient" and "start" in changes and row["status"] == "scheduled"
        and fee_really_paid(row, patient) and _now() >= ctx["start"] - timedelta(hours=24)
    )
    return {
        **ctx,
        "new": {"doctor": doctor, "modality": modality, "start": start, "minutes": minutes,
                "note": note, "ctype": ctype, "billing": billing},
        "changes": changes,
        # pending_reschedule sem evento no Calendar: remarcar sempre avisa o paciente.
        "notify": bool(changes & _NOTIFY_FIELDS) or row["status"] == "pending_reschedule",
        "late_fee": late_fee,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_panel_appointments.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_appointments.py tests/test_panel_appointments.py
git commit -m "feat(painel): validação da alteração de consulta"
```

---

### Task 6: aplicar a alteração (Calendar, banco, desfazer)

Três caminhos. Mesmo médico e consulta `scheduled`: `update_event` no mesmo evento. Médico trocado: cria evento no calendário novo, grava, depois apaga o antigo. Consulta `pending_reschedule` (sem evento): cria evento e volta a `scheduled`. Se o banco falhar, o Calendar é desfeito.

**Files:**
- Modify: `app/panel_appointments.py`
- Test: `tests/test_panel_appointments.py`

- [ ] **Step 1: Write the failing tests**

Acrescente:

```python
# ── apply_edit ─────────────────────────────────────────────────────────────

def cal_patches(update_row=None):
    cal = {
        "cal_id": patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock,
                        side_effect=lambda d: f"cal-{d}"),
        "update_event": patch("app.google_calendar.update_event", new_callable=AsyncMock),
        "create_event": patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-new"),
        "cancel_event": patch("app.google_calendar.cancel_event", new_callable=AsyncMock),
        "update_row": patch("app.panel_appointments._update_row", new_callable=AsyncMock, side_effect=update_row),
        "log_event": patch("app.panel_appointments.log_event", new_callable=AsyncMock),
        "notify": patch("app.panel_appointments._notify_clinic_async"),
    }
    return cal


async def run_apply(req, update_row=None):
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(update_row).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        out = await pa.apply_edit(req)
    return out, m


@pytest.mark.asyncio
async def test_apply_edit_same_doctor_updates_event_and_resets_confirmations():
    req = await build_edit()
    out, m = await run_apply(req)
    m["update_event"].assert_awaited_once()
    kw = m["update_event"].call_args.kwargs
    assert kw["calendar_id"] == "cal-julio" and kw["event_id"] == "evt1"
    assert kw["new_start"] == datetime(2026, 10, 6, 9, 0, tzinfo=TZ)
    row_id, fields = m["update_row"].call_args[0]
    assert row_id == "uuid-1"
    assert fields["confirmed_at"] is None and fields["reminder_day_before_sent_at"] is None
    assert fields["reminder_day_of_sent_at"] is None and fields["reschedule_initiated_by"] == "clinic"
    assert out["appointment_id"] == "evt1"
    logged = [c[0][0] for c in m["log_event"].call_args_list]
    assert logged == ["appointment_edited", "appointment_rescheduled"]
    m["notify"].assert_called_once()


@pytest.mark.asyncio
async def test_apply_edit_note_only_keeps_confirmation_and_skips_email():
    req = await build_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"})
    out, m = await run_apply(req)
    fields = m["update_row"].call_args[0][1]
    assert "confirmed_at" not in fields and fields["session_note"] == "Domiciliar"
    assert m["update_event"].call_args.kwargs["session_note"] == "Domiciliar"
    assert [c[0][0] for c in m["log_event"].call_args_list] == ["appointment_edited"]
    m["notify"].assert_not_called()


@pytest.mark.asyncio
async def test_apply_edit_doctor_change_creates_new_event_then_deletes_old():
    req = await build_edit({"doctor": "bruna", "start": "2026-10-05T09:00"})
    out, m = await run_apply(req)
    assert m["create_event"].call_args.kwargs["calendar_id"] == "cal-bruna"
    fields = m["update_row"].call_args[0][1]
    assert fields["appointment_id"] == "evt-new" and fields["doctor_id"] == BRUNA
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["appointment_id"] == "evt-new"


@pytest.mark.asyncio
async def test_apply_edit_pending_reschedule_creates_event_and_schedules():
    req = await build_edit(row={**ROW, "status": "pending_reschedule"})
    out, m = await run_apply(req)
    m["create_event"].assert_awaited_once()
    m["update_event"].assert_not_called()
    fields = m["update_row"].call_args[0][1]
    assert fields["status"] == "scheduled" and fields["reschedule_requested_at"] is None
    m["cancel_event"].assert_not_called()


@pytest.mark.asyncio
async def test_apply_edit_db_failure_reverts_calendar_update():
    req = await build_edit()
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError):
            await pa.apply_edit(req)
    assert m["update_event"].await_count == 2
    assert m["update_event"].call_args.kwargs["new_start"] == datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_apply_edit_db_failure_deletes_created_event():
    req = await build_edit({"doctor": "bruna", "start": "2026-10-05T09:00"})
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError):
            await pa.apply_edit(req)
    m["cancel_event"].assert_awaited_once_with("cal-bruna", "evt-new")


@pytest.mark.asyncio
async def test_apply_edit_calendar_failure_changes_nothing():
    req = await build_edit()
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches().items()}
        m["update_event"].side_effect = RuntimeError("gcal")
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        with pytest.raises(BookingError, match="nada foi alterado"):
            await pa.apply_edit(req)
    m["update_row"].assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_panel_appointments.py -q -k apply_edit`
Expected: FAIL (`AttributeError: ... has no attribute 'apply_edit'`).

- [ ] **Step 3: Implement**

Acrescente a `app/panel_appointments.py`:

```python
def _event_kwargs(req: dict, doctor: str, start: datetime, minutes: int, note: str, modality: str) -> dict:
    patient = req["patient"]
    return {
        "slot_minutes": minutes, "patient_name": _display(patient), "doctor_name": DOCTOR_LABELS[doctor],
        "session_note": note, "modality": modality, "patient_email": patient.get("email") or "",
        "patient_number": req["phone"],
    }


async def apply_edit(req: dict) -> dict:
    """Altera a mesma linha. Calendar primeiro; se o banco falhar, o Calendar volta."""
    from app.google_calendar import cancel_event, create_event, update_event
    from app.graph.tools import _get_doctor_calendar_id

    row, new, patient = req["row"], req["new"], req["patient"]
    old_doctor, new_doctor = req["doctor"], new["doctor"]
    now_iso = _now().isoformat()
    try:
        new_cal = await _get_doctor_calendar_id(new_doctor)
    except Exception as exc:
        raise BookingError("calendário do médico não encontrado; nada foi alterado") from exc
    if not new_cal:
        raise BookingError("calendário do médico não encontrado; nada foi alterado")

    fields = {
        "start_time": new["start"].isoformat(),
        "end_time": (new["start"] + timedelta(minutes=new["minutes"])).isoformat(),
        "doctor_id": DOCTOR_IDS[new_doctor], "modality": new["modality"],
        "session_note": new["note"] or None, "consultation_type": new["ctype"],
        "updated_at": now_iso, **billing_update(row, patient, new["billing"], now_iso),
    }
    pending = row["status"] == "pending_reschedule"
    if pending or req["changes"] & {"start", "doctor"}:
        # Confirmação e lembretes valem para a data antiga (ver caso Isaac, reschedule_appointment).
        fields.update({"confirmed_at": None, "reminder_day_before_sent_at": None,
                       "reminder_day_of_sent_at": None, "reschedule_initiated_by": req["initiated_by"]})
    if pending:
        fields.update({"status": "scheduled", "reschedule_requested_at": None})

    ev = _event_kwargs(req, new_doctor, new["start"], new["minutes"], new["note"], new["modality"])
    created = None
    try:
        if pending or new_doctor != old_doctor:
            created = await create_event(calendar_id=new_cal, start=new["start"], **ev)
            fields["appointment_id"] = created
        else:
            await update_event(calendar_id=new_cal, event_id=row["appointment_id"], new_start=new["start"], **ev)
    except Exception as exc:
        _logger.exception("panel edit: Calendar falhou appt=%s", row["appointment_id"])
        raise BookingError("não foi possível atualizar a agenda; nada foi alterado") from exc

    try:
        await _update_row(row["id"], fields)
    except Exception as exc:
        _logger.exception("panel edit: banco falhou appt=%s", row["appointment_id"])
        try:
            if created:
                await cancel_event(new_cal, created)
            else:
                old = _event_kwargs(req, old_doctor, req["start"], req["minutes"],
                                    row.get("session_note") or "", row.get("modality") or "")
                await update_event(calendar_id=new_cal, event_id=row["appointment_id"], new_start=req["start"], **old)
        except Exception:
            _logger.exception("panel edit: falha ao desfazer o Calendar appt=%s", row["appointment_id"])
        raise BookingError("não foi possível gravar a alteração; nada foi alterado") from exc

    warnings = []
    if created and not pending:
        # Médico trocado: o evento antigo sai do calendário do médico anterior.
        try:
            await cancel_event(await _get_doctor_calendar_id(old_doctor), row["appointment_id"])
        except Exception:
            _logger.exception("panel edit: evento antigo ficou no Calendar appt=%s", row["appointment_id"])
            warnings.append("o horário antigo continua na agenda do médico anterior; apague à mão")

    appointment_id = fields.get("appointment_id", row["appointment_id"])
    meta = {"origem": "painel", "atendente": req["agent"], "encaixe": bool(req.get("encaixe")),
            "initiated_by": req["initiated_by"]}
    await log_event("appointment_edited", req["phone"], {
        "appointment_id": appointment_id, "changes": sorted(req["changes"]), **meta})
    if pending or req["changes"] & {"start", "doctor"}:
        # Mesmo evento que reschedule_appointment grava: a política de 1 remarcação conta por ele.
        await log_event("appointment_rescheduled", req["phone"], {
            "appointment_id": appointment_id, "new_datetime": new["start"].replace(tzinfo=None).isoformat(),
            "fee_paid": bool(row.get("booking_fee_paid_at") or row.get("booking_fee_waived")), **meta})

    old_line = _line(old_doctor, req["start"], row.get("session_note") or "", row.get("modality") or "", req["minutes"])
    new_line = _line(new_doctor, new["start"], new["note"], new["modality"], new["minutes"])
    if req["notify"]:
        who = "Clínica" if req["initiated_by"] == "clinic" else "Paciente"
        _notify_clinic_async(
            f"Agendamento alterado — {_display(patient)}",
            "Agendamento alterado pelo painel 🔄\n"
            f"Paciente: {_display(patient)}\nHorário anterior: {old_line}\nNovo horário: {new_line}\n"
            f"Quem pediu: {who}\nAtendente: {req['agent'] or '—'}"
            + ("\n⚠️ Encaixe fora da grade" if req.get("encaixe") else ""),
            req["phone"],
        )
    return {"appointment_id": appointment_id, "old_line": old_line, "new_line": new_line,
            "new_fee": False, "warnings": warnings}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_panel_appointments.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_appointments.py tests/test_panel_appointments.py
git commit -m "feat(painel): alteração de consulta no Calendar e no banco, com desfazer"
```

---

### Task 7: remarcação tardia e `handle_edit`

**Files:**
- Modify: `app/panel_appointments.py`
- Test: `tests/test_panel_appointments.py`

- [ ] **Step 1: Write the failing tests**

Acrescente:

```python
# ── remarcação tardia ──────────────────────────────────────────────────────

LATE_ROW = {**ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00",
            "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}


@pytest.mark.asyncio
async def test_late_reschedule_cancels_old_row_and_creates_new_normal_fee():
    req = await build_edit({"initiated_by": "patient"}, row=LATE_ROW)
    created = {"kind": "normal", "lines": ["l"], "pending_part2": False,
               "appointments": [{"appointment_id": "evt-new"}]}
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches().items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        mock_create = st.enter_context(patch("app.panel_appointments.create_appointments",
                                             new_callable=AsyncMock, return_value=created))
        out = await pa.apply_late_reschedule(req)
    booking = mock_create.call_args[0][0]
    assert booking["billing"] == "normal" and booking["contact_id"] == "c1"
    assert booking["parts"] == [{"start": datetime(2026, 10, 6, 9, 0, tzinfo=TZ), "minutes": 60}]
    assert m["update_row"].call_args[0] == ("uuid-1", {"status": "canceled", "updated_at": NOW.isoformat()})
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["appointment_id"] == "evt-new" and out["new_fee"] is True


@pytest.mark.asyncio
async def test_late_reschedule_old_row_failure_rolls_back_new_one():
    req = await build_edit({"initiated_by": "patient"}, row=LATE_ROW)
    created = {"kind": "normal", "lines": ["l"], "pending_part2": False,
               "appointments": [{"appointment_id": "evt-new"}]}
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cal_patches(RuntimeError("db")).items()}
        st.enter_context(patch("app.panel_appointments._now", return_value=NOW))
        st.enter_context(patch("app.panel_appointments.create_appointments", new_callable=AsyncMock, return_value=created))
        mock_del = st.enter_context(patch("app.panel_appointments._delete_row", new_callable=AsyncMock))
        with pytest.raises(BookingError):
            await pa.apply_late_reschedule(req)
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt-new")
    mock_del.assert_awaited_once_with("evt-new")


# ── handle_edit ────────────────────────────────────────────────────────────

async def run_handle_edit(body, check=(), row=None, patient=None, applied=None):
    with ExitStack() as st:
        for p in load_patches(row=row, patient=patient):
            st.enter_context(p)
        mock_check = st.enter_context(patch("app.panel_appointments.check_slot", new_callable=AsyncMock, return_value=list(check)))
        mock_prev = st.enter_context(patch("app.panel_appointments.preview_message", new_callable=AsyncMock,
                                           return_value={"text": "t", "recipients": [], "held": []}))
        mock_apply = st.enter_context(patch("app.panel_appointments.apply_edit", new_callable=AsyncMock,
                                            return_value=applied or {"appointment_id": "evt1", "old_line": "a",
                                                                     "new_line": "b", "new_fee": False, "warnings": []}))
        mock_late = st.enter_context(patch("app.panel_appointments.apply_late_reschedule", new_callable=AsyncMock,
                                           return_value={"appointment_id": "evt-new", "old_line": "a",
                                                         "new_line": "b", "new_fee": True, "warnings": []}))
        mock_send = st.enter_context(patch("app.panel_appointments.deliver_message", new_callable=AsyncMock,
                                           return_value={"sent": ["Ana"], "not_delivered": [], "held": []}))
        status, payload = await pa.handle_edit({**EDIT, **body})
    return status, payload, {"check": mock_check, "prev": mock_prev, "apply": mock_apply,
                             "late": mock_late, "send": mock_send}


@pytest.mark.asyncio
async def test_handle_edit_dry_run_reports_encaixe_and_late_fee():
    status, payload, m = await run_handle_edit({"dry_run": True, "initiated_by": "patient"},
                                               check=["dia bloqueado na agenda"], row=LATE_ROW)
    assert status == 200
    assert payload["encaixe_reasons"] == ["dia bloqueado na agenda"]
    assert payload["late_fee"] is True and payload["notify"] is True
    assert m["check"].call_args.kwargs["exclude_appointment_id"] == "evt1"
    m["apply"].assert_not_called()


@pytest.mark.asyncio
async def test_handle_edit_needs_encaixe_409():
    status, payload, m = await run_handle_edit({}, check=["fora do horário de atendimento"])
    assert status == 409 and payload["detail"]["needs_encaixe"] is True
    m["apply"].assert_not_called()


@pytest.mark.asyncio
async def test_handle_edit_encaixe_confirmed_skips_check():
    status, payload, m = await run_handle_edit({"encaixe_confirmed": True})
    assert status == 200
    m["check"].assert_not_called()
    assert m["apply"].call_args[0][0]["encaixe"] is True


@pytest.mark.asyncio
async def test_handle_edit_note_only_no_check_no_message():
    status, payload, m = await run_handle_edit({"start": "2026-10-05T09:00", "session_note": "Domiciliar"})
    assert status == 200
    m["check"].assert_not_called()
    m["send"].assert_not_called()
    assert payload["message"] == {"sent": [], "not_delivered": [], "held": [], "skipped": True}


@pytest.mark.asyncio
async def test_handle_edit_late_goes_to_late_path_and_message_has_new_fee():
    status, payload, m = await run_handle_edit({"initiated_by": "patient"}, row=LATE_ROW)
    assert status == 200 and payload["appointment_id"] == "evt-new"
    m["late"].assert_awaited_once()
    m["apply"].assert_not_called()
    text_for = m["send"].call_args[0][2]
    assert "nova taxa" in text_for({"name": "Ana"})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_panel_appointments.py -q -k "late or handle_edit"`
Expected: FAIL (`has no attribute 'apply_late_reschedule'`).

- [ ] **Step 3: Implement**

Acrescente:

```python
async def apply_late_reschedule(req: dict) -> dict:
    """Paciente pediu para remarcar com menos de 24h e a taxa foi paga: a taxa fica
    retida na linha antiga (canceled) e nasce uma linha nova com a cobrança escolhida
    (normal = nova taxa). Mesmo resultado de cancel_appointment + confirm_appointment
    que reschedule_appointment manda a Eva fazer nesse caso."""
    from app.google_calendar import cancel_event
    from app.graph.tools import _get_doctor_calendar_id

    row, new, patient = req["row"], req["new"], req["patient"]
    booking = {
        "phone": req["phone"], "contact_id": req["recipient_contact_id"], "patient": patient,
        "doctor": new["doctor"], "modality": new["modality"],
        "parts": [{"start": new["start"], "minutes": new["minutes"]}],
        "split": False, "split_of": None, "session_note": new["note"],
        "first_consultation": new["ctype"] == "primeira_consulta", "billing": new["billing"],
        "encaixe": bool(req.get("encaixe")), "agent": req["agent"],
    }
    created = await create_appointments(booking)  # tudo ou nada; BookingError sobe
    new_id = created["appointments"][0]["appointment_id"]
    now_iso = _now().isoformat()
    try:
        await _update_row(row["id"], {"status": "canceled", "updated_at": now_iso})
    except Exception as exc:
        _logger.exception("panel late reschedule: falha ao cancelar a linha antiga appt=%s", row["appointment_id"])
        try:
            await cancel_event(await _get_doctor_calendar_id(new["doctor"]), new_id)
            await _delete_row(new_id)
        except Exception:
            _logger.exception("panel late reschedule: falha ao desfazer a linha nova %s", new_id)
        raise BookingError("não foi possível concluir a remarcação; confira a lista de consultas") from exc

    warnings = []
    try:
        await cancel_event(await _get_doctor_calendar_id(req["doctor"]), row["appointment_id"])
    except Exception:
        _logger.exception("panel late reschedule: evento antigo ficou no Calendar appt=%s", row["appointment_id"])
        warnings.append("o horário antigo continua na agenda; apague à mão")
    await log_event("appointment_canceled", req["phone"], {
        "appointment_id": row["appointment_id"], "preserve_fee": False, "origem": "painel",
        "atendente": req["agent"], "initiated_by": "patient", "fee_action": "reter",
        "reason": "remarcação com menos de 24h a pedido do paciente", "replaced_by": new_id,
    })
    return {
        "appointment_id": new_id,
        "old_line": _line(req["doctor"], req["start"], row.get("session_note") or "", row.get("modality") or "", req["minutes"]),
        "new_line": _line(new["doctor"], new["start"], new["note"], new["modality"], new["minutes"]),
        "new_fee": created["kind"] == "normal",
        "warnings": warnings,
    }


_NO_MESSAGE = {"sent": [], "not_delivered": [], "held": [], "skipped": True}


async def handle_edit(body: dict) -> tuple[int, dict]:
    """Fluxo de POST /admin/panel/appointments/edit. Devolve (status_http, corpo)."""
    req = await build_edit(body)
    new, row = req["new"], req["row"]
    reasons: list[str] = []
    moves = bool(req["changes"] & _SLOT_FIELDS) or row["status"] == "pending_reschedule"
    if moves and not body.get("encaixe_confirmed"):
        reasons = await check_slot(new["doctor"], new["start"], new["minutes"], req["patient"]["id"],
                                   exclude_appointment_id=row["appointment_id"])

    def text_for(lines: tuple[str, str], new_fee: bool):
        return lambda c: change_text(req["initiated_by"], contact_first_name(c), lines[0], lines[1], new_fee)

    if body.get("dry_run"):
        message = None
        if req["notify"]:
            old_line = _line(req["doctor"], req["start"], row.get("session_note") or "",
                             row.get("modality") or "", req["minutes"])
            new_line = _line(new["doctor"], new["start"], new["note"], new["modality"], new["minutes"])
            new_fee = req["late_fee"] and new["billing"] == "normal"
            message = await preview_message(req["patient"]["id"], req["recipient_contact_id"],
                                            text_for((old_line, new_line), new_fee))
        return 200, {"encaixe_reasons": reasons, "late_fee": req["late_fee"],
                     "notify": req["notify"], "message": message}

    if reasons:
        return 409, {"detail": {"needs_encaixe": True, "reasons": reasons}}

    req["encaixe"] = bool(body.get("encaixe_confirmed"))
    result = await (apply_late_reschedule if req["late_fee"] else apply_edit)(req)
    if req["notify"]:
        msg = await deliver_message(req["patient"]["id"], req["recipient_contact_id"],
                                    text_for((result["old_line"], result["new_line"]), result["new_fee"]),
                                    _display(req["patient"]), new["doctor"])
    else:
        msg = dict(_NO_MESSAGE)
    return 200, {"appointment_id": result["appointment_id"], "warnings": result["warnings"], "message": msg}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_panel_appointments.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_appointments.py tests/test_panel_appointments.py
git commit -m "feat(painel): remarcação tardia com nova taxa e fluxo da alteração"
```

---

### Task 8: cancelar (taxa, reembolso, partes da 1ª consulta)

**Files:**
- Modify: `app/panel_appointments.py`
- Test: `tests/test_panel_appointments.py`

- [ ] **Step 1: Write the failing tests**

Acrescente:

```python
# ── cancelar ───────────────────────────────────────────────────────────────

PAID_ROW = {**ROW, "booking_fee_paid_at": "2026-09-30T10:00:00-03:00"}   # consulta 05/10, NOW 01/10: > 24h
CANCEL = {"phone": "5581999998888", "appointment_id": "evt1", "initiated_by": "patient",
          "fee_action": None, "reason": "", "both_parts": False, "agent": "Maria"}


async def build_cancel(body=None, **kw):
    with ExitStack() as st:
        for p in load_patches(**kw):
            st.enter_context(p)
        return await pa.build_cancel({**CANCEL, **(body or {})})


@pytest.mark.asyncio
async def test_build_cancel_requires_initiated_by_unless_dry_run():
    with pytest.raises(PanelInputError, match="quem pediu"):
        await build_cancel({"initiated_by": None})
    req = await build_cancel({"initiated_by": None, "dry_run": True})
    assert req["initiated_by"] is None


@pytest.mark.asyncio
async def test_build_cancel_paid_fee_requires_choice():
    with pytest.raises(PanelInputError, match="taxa"):
        await build_cancel(row=PAID_ROW)
    req = await build_cancel({"fee_action": "devolver"}, row=PAID_ROW)
    assert req["fee_paid"] is True and req["policy_late"] is False


@pytest.mark.asyncio
async def test_build_cancel_fee_action_ignored_when_not_paid():
    req = await build_cancel({"fee_action": "devolver"})
    assert req["fee_paid"] is False and req["fee_action"] is None


@pytest.mark.asyncio
async def test_build_cancel_late_refund_needs_reason():
    late = {**PAID_ROW, "start_time": "2026-10-01T20:00:00+00:00", "end_time": "2026-10-01T21:00:00+00:00"}
    with pytest.raises(PanelInputError, match="motivo"):
        await build_cancel({"fee_action": "devolver"}, row=late)
    req = await build_cancel({"fee_action": "devolver", "reason": "exceção aprovada pelo Dr. Júlio"}, row=late)
    assert req["policy_late"] is True


@pytest.mark.asyncio
async def test_build_cancel_credit_not_allowed_on_pending_reschedule():
    with pytest.raises(PanelInputError, match="crédito"):
        await build_cancel({"fee_action": "credito"}, row={**PAID_ROW, "status": "pending_reschedule"})


@pytest.mark.asyncio
async def test_build_cancel_refund_already_requested():
    with pytest.raises(PanelInputError, match="já foi pedida"):
        await build_cancel({"fee_action": "devolver"}, row={**PAID_ROW, "refund_requested_at": "x"})


@pytest.mark.asyncio
async def test_build_cancel_finds_split_sibling():
    part1 = {**ROW, "session_note": "1ª consulta · parte 1 de 2"}
    part2 = {**ROW, "id": "uuid-2", "appointment_id": "evt2", "session_note": "1ª consulta · parte 2 de 2",
             "start_time": "2026-10-08T12:00:00+00:00", "end_time": "2026-10-08T13:00:00+00:00"}
    req = await build_cancel({"both_parts": True}, row=part1, patient=KID, sibling_rows=[part1, part2])
    assert req["sibling"]["appointment_id"] == "evt2" and req["both_parts"] is True


def cancel_patches(update_row=None, cancel_event=None, sheet=None):
    return {
        "cal_id": patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"),
        "cancel_event": patch("app.google_calendar.cancel_event", new_callable=AsyncMock, side_effect=cancel_event),
        "update_row": patch("app.panel_appointments._update_row", new_callable=AsyncMock, side_effect=update_row),
        "log_event": patch("app.panel_appointments.log_event", new_callable=AsyncMock),
        "notify": patch("app.panel_appointments._notify_clinic_async"),
        "sheet": patch("app.google_sheets.append_document_request", new_callable=AsyncMock, side_effect=sheet),
        "now": patch("app.panel_appointments._now", return_value=NOW),
    }


async def run_cancel(req, **kw):
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cancel_patches(**kw).items()}
        out = await pa.apply_cancel(req)
    return out, m


@pytest.mark.asyncio
async def test_apply_cancel_db_first_then_calendar():
    req = await build_cancel()
    out, m = await run_cancel(req)
    assert m["update_row"].call_args_list[0][0] == ("uuid-1", {"status": "canceled", "updated_at": NOW.isoformat()})
    m["cancel_event"].assert_awaited_once_with("cal-julio", "evt1")
    assert out["canceled"] == ["evt1"]
    assert m["log_event"].call_args[0][0] == "appointment_canceled"


@pytest.mark.asyncio
async def test_apply_cancel_calendar_failure_restores_status():
    req = await build_cancel()
    with ExitStack() as st:
        m = {k: st.enter_context(p) for k, p in cancel_patches(cancel_event=RuntimeError("gcal")).items()}
        with pytest.raises(BookingError, match="nada foi alterado"):
            await pa.apply_cancel(req)
    assert m["update_row"].call_args_list[1][0][1]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_apply_cancel_event_already_gone_is_success():
    gone = Exception("gone")
    gone.resp = MagicMock(status=410)
    req = await build_cancel()
    out, m = await run_cancel(req, cancel_event=gone)
    assert out["canceled"] == ["evt1"]
    assert len(m["update_row"].call_args_list) == 1


@pytest.mark.asyncio
async def test_apply_cancel_credit_keeps_fee_as_pending_reschedule():
    req = await build_cancel({"fee_action": "credito"}, row=PAID_ROW)
    out, m = await run_cancel(req)
    assert m["update_row"].call_args_list[0][0][1]["status"] == "pending_reschedule"
    m["sheet"].assert_not_called()


@pytest.mark.asyncio
async def test_apply_cancel_refund_marks_and_writes_sheet():
    req = await build_cancel({"fee_action": "devolver", "reason": "mudou de cidade"}, row=PAID_ROW)
    out, m = await run_cancel(req)
    refund_fields = m["update_row"].call_args_list[1][0][1]
    assert refund_fields["refund_requested_at"] == NOW.isoformat()
    kw = m["sheet"].call_args.kwargs
    assert kw["document_type"] == "Solicitação de Reembolso"
    assert kw["medication_note"] == "Valor: R$ 100,00 | Consulta: 05/10/2026 às 09:00 | Motivo: mudou de cidade"
    assert kw["patient_cpf"] == "123"
    logged = [c[0][0] for c in m["log_event"].call_args_list]
    assert "refund_requested" in logged


@pytest.mark.asyncio
async def test_apply_cancel_sheet_failure_becomes_warning():
    req = await build_cancel({"fee_action": "devolver"}, row=PAID_ROW)
    out, m = await run_cancel(req, sheet=RuntimeError("sheets"))
    assert out["canceled"] == ["evt1"]
    assert any("planilha" in w for w in out["warnings"])


@pytest.mark.asyncio
async def test_apply_cancel_both_parts_refund_once():
    part1 = {**PAID_ROW, "session_note": "1ª consulta · parte 1 de 2"}
    part2 = {**PAID_ROW, "id": "uuid-2", "appointment_id": "evt2", "session_note": "1ª consulta · parte 2 de 2"}
    req = await build_cancel({"both_parts": True, "fee_action": "devolver"}, row=part1, patient=KID,
                             sibling_rows=[part1, part2])
    out, m = await run_cancel(req)
    assert out["canceled"] == ["evt1", "evt2"]
    m["sheet"].assert_awaited_once()


# ── handle_cancel ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_cancel_dry_run_info_without_initiated_by():
    with ExitStack() as st:
        for p in load_patches(row=PAID_ROW):
            st.enter_context(p)
        mock_prev = st.enter_context(patch("app.panel_appointments.preview_message", new_callable=AsyncMock))
        status, payload = await pa.handle_cancel({**CANCEL, "initiated_by": None, "dry_run": True})
    assert status == 200
    assert payload["fee_paid"] is True and payload["policy_late"] is False
    assert payload["status"] == "scheduled" and payload["sibling"] is None
    assert payload["message"] is None
    mock_prev.assert_not_called()


@pytest.mark.asyncio
async def test_handle_cancel_sends_message_with_fee_line():
    with ExitStack() as st:
        for p in load_patches(row=PAID_ROW):
            st.enter_context(p)
        st.enter_context(patch("app.panel_appointments.apply_cancel", new_callable=AsyncMock,
                               return_value={"canceled": ["evt1"], "lines": ["l1"], "warnings": []}))
        mock_send = st.enter_context(patch("app.panel_appointments.deliver_message", new_callable=AsyncMock,
                                           return_value={"sent": ["Ana"], "not_delivered": [], "held": []}))
        status, payload = await pa.handle_cancel({**CANCEL, "fee_action": "credito"})
    assert status == 200 and payload["canceled"] == ["evt1"]
    text = mock_send.call_args[0][2]({"name": "Ana Souza"})
    assert text.startswith("Olá, Ana! Conforme combinado, sua consulta foi cancelada:\nl1")
    assert "fica guardado" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_panel_appointments.py -q -k cancel`
Expected: FAIL (`has no attribute 'build_cancel'`).

- [ ] **Step 3: Implement**

Acrescente:

```python
_FEE_ACTIONS = ("devolver", "credito", "reter")


async def _split_sibling(row: dict) -> dict | None:
    """A outra parte ativa da 1ª consulta dividida, se houver."""
    label = _split_label(row.get("session_note"))
    if not label:
        return None
    other = SPLIT_PART2 if label == SPLIT_PART1 else SPLIT_PART1
    for r in await _fetch_active_rows(row["patient_id"]):
        if r["appointment_id"] != row["appointment_id"] and (r.get("session_note") or "").startswith(other):
            return r
    return None


async def build_cancel(body: dict) -> dict:
    """Valida o cancelamento. Em dry_run só lê: quem pediu e a taxa podem faltar."""
    ctx = await _load(body)
    row, patient = ctx["row"], ctx["patient"]
    paid = fee_really_paid(row, patient)
    hours = (ctx["start"] - _now()).total_seconds() / 3600
    sibling = await _split_sibling(row)
    fee_action = body.get("fee_action") if paid else None
    reason = (body.get("reason") or "").strip()[:200]
    if not body.get("dry_run"):
        if ctx["initiated_by"] is None:
            raise PanelInputError("informe quem pediu o cancelamento: paciente ou clínica")
        if paid and fee_action not in _FEE_ACTIONS:
            raise PanelInputError("escolha o que fazer com a taxa paga")
        if fee_action == "credito" and row["status"] == "pending_reschedule":
            raise PanelInputError("a taxa desta consulta já está guardada como crédito")
        if fee_action == "devolver" and row.get("refund_requested_at"):
            raise PanelInputError("a devolução desta taxa já foi pedida")
        if fee_action == "devolver" and hours < 24 and not reason:
            raise PanelInputError("com menos de 24h, informe o motivo da devolução")
    return {**ctx, "fee_paid": paid, "hours_until": hours, "policy_late": hours < 24,
            "sibling": sibling, "fee_action": fee_action, "reason": reason,
            "both_parts": bool(body.get("both_parts")) and sibling is not None}


def _already_gone(exc: Exception) -> bool:
    """Evento já apagado à mão no Calendar (404/410): o cancelamento segue."""
    return getattr(getattr(exc, "resp", None), "status", None) in (404, 410)


async def _cancel_row(row: dict, new_status: str) -> None:
    """Banco primeiro, depois Calendar. Se o Calendar falhar, o status volta."""
    from app.google_calendar import cancel_event
    from app.graph.tools import _get_doctor_calendar_id

    now_iso = _now().isoformat()
    try:
        await _update_row(row["id"], {"status": new_status, "updated_at": now_iso})
    except Exception as exc:
        _logger.exception("panel cancel: banco falhou appt=%s", row["appointment_id"])
        raise BookingError("não foi possível gravar o cancelamento; nada foi alterado") from exc
    if row["status"] != "scheduled":
        return  # pending_reschedule: o evento já saiu do Calendar
    try:
        cal = await _get_doctor_calendar_id(_DOCTOR_BY_ID.get(row["doctor_id"], ""))
        await cancel_event(cal, row["appointment_id"])
    except Exception as exc:
        if _already_gone(exc):
            return
        _logger.exception("panel cancel: Calendar falhou appt=%s", row["appointment_id"])
        try:
            await _update_row(row["id"], {"status": row["status"], "updated_at": now_iso})
        except Exception:
            _logger.exception("panel cancel: falha ao restaurar status appt=%s", row["appointment_id"])
        raise BookingError("não foi possível tirar a consulta da agenda; nada foi alterado") from exc


async def _register_refund(req: dict) -> list[str]:
    """Mesma gravação de register_refund_request: refund_requested_at e linha
    "Solicitação de Reembolso" na planilha de Solicitações. A baixa depois segue
    confirm_refund_completed. Falhas aqui viram aviso: a consulta já foi cancelada."""
    from app.google_sheets import append_document_request

    row, patient = req["row"], req["patient"]
    warnings = []
    now_iso = _now().isoformat()
    try:
        await _update_row(row["id"], {"refund_requested_at": now_iso, "updated_at": now_iso})
    except Exception:
        _logger.exception("panel cancel: refund_requested_at falhou appt=%s", row["appointment_id"])
        warnings.append("o pedido de devolução não foi marcado na consulta; avise a equipe")
    when = req["start"].strftime("%d/%m/%Y às %H:%M")
    reason = req["reason"] or "cancelamento pelo painel"
    try:
        await append_document_request(
            patient_name=_display(patient),
            patient_age=_age_on(patient.get("birth_date"), _now().date()),
            phone=req["phone"],
            patient_email=patient.get("email") or "",
            document_type="Solicitação de Reembolso",
            medication_note=f"Valor: R$ {BOOKING_FEE} | Consulta: {when} | Motivo: {reason}",
            doctor_name=DOCTOR_LABELS.get(req["doctor"], "médico(a)"),
            patient_cpf=patient.get("patient_cpf") or "",
        )
    except Exception:
        _logger.exception("panel cancel: planilha de Solicitações falhou appt=%s", row["appointment_id"])
        warnings.append("a devolução não entrou na planilha de Solicitações; registre à mão")
    await log_event("refund_requested", req["phone"], {
        "appointment_id": row["appointment_id"], "amount": BOOKING_FEE, "reason": reason,
        "origem": "painel", "atendente": req["agent"]})
    return warnings


def _row_line(row: dict) -> str:
    start = _to_local(row["start_time"])
    return format_appt_line(_DOCTOR_BY_ID.get(row["doctor_id"], ""), start, row.get("session_note") or "")


async def apply_cancel(req: dict) -> dict:
    rows = [req["row"]] + ([req["sibling"]] if req["both_parts"] else [])
    new_status = "pending_reschedule" if req["fee_action"] == "credito" else "canceled"
    done: list[str] = []
    for r in rows:
        try:
            await _cancel_row(r, new_status)
        except BookingError:
            if done:
                raise BookingError("a 1ª parte foi cancelada, mas a outra não; confira a lista de consultas")
            raise
        done.append(r["appointment_id"])
        await log_event("appointment_canceled", req["phone"], {
            "appointment_id": r["appointment_id"], "preserve_fee": new_status == "pending_reschedule",
            "origem": "painel", "atendente": req["agent"], "initiated_by": req["initiated_by"],
            "fee_action": req["fee_action"], "reason": req["reason"]})

    warnings = await _register_refund(req) if req["fee_action"] == "devolver" else []

    lines = [_row_line(r) for r in rows]
    who = "Clínica" if req["initiated_by"] == "clinic" else "Paciente"
    fee_note = {"devolver": "Taxa: devolver ao paciente (ver planilha de Solicitações)",
                "credito": "Taxa: guardada para remarcar", "reter": "Taxa: retida"}.get(req["fee_action"] or "", "")
    title = "Consulta liberada para remarcação 🔄" if new_status == "pending_reschedule" else "Agendamento cancelado pelo painel ❌"
    _notify_clinic_async(
        f"{'Consulta liberada para remarcação' if new_status == 'pending_reschedule' else 'Agendamento cancelado'} — {_display(req['patient'])}",
        f"{title}\nPaciente: {_display(req['patient'])}\n" + "\n".join(lines)
        + f"\nQuem pediu: {who}" + (f"\n{fee_note}" if fee_note else "")
        + (f"\nMotivo: {req['reason']}" if req["reason"] else "") + f"\nAtendente: {req['agent'] or '—'}",
        req["phone"],
    )
    return {"canceled": done, "lines": lines, "warnings": warnings}


async def handle_cancel(body: dict) -> tuple[int, dict]:
    """Fluxo de POST /admin/panel/appointments/cancel. Devolve (status_http, corpo)."""
    req = await build_cancel(body)

    def text_for(lines: list[str]):
        return lambda c: cancel_text(req["initiated_by"], contact_first_name(c), lines, req["fee_action"])

    if body.get("dry_run"):
        sib = req["sibling"]
        lines = [_row_line(req["row"])] + ([_row_line(sib)] if req["both_parts"] else [])
        message = None
        if req["initiated_by"]:
            message = await preview_message(req["patient"]["id"], req["recipient_contact_id"], text_for(lines))
        return 200, {
            "fee_paid": req["fee_paid"], "status": req["row"]["status"],
            "hours_until": round(req["hours_until"], 1), "policy_late": req["policy_late"],
            "sibling": {"appointment_id": sib["appointment_id"], "line": _row_line(sib)} if sib else None,
            "message": message,
        }

    result = await apply_cancel(req)
    msg = await deliver_message(req["patient"]["id"], req["recipient_contact_id"], text_for(result["lines"]),
                                _display(req["patient"]), req["doctor"])
    return 200, {"canceled": result["canceled"], "warnings": result["warnings"], "message": msg}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_panel_appointments.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_appointments.py tests/test_panel_appointments.py
git commit -m "feat(painel): cancelamento com taxa, reembolso e partes da 1ª consulta"
```

---

### Task 9: endpoints na Eva

**Files:**
- Modify: `app/main.py` (perto de `admin_panel_appointments`, ~linha 498)
- Test: `tests/test_webhook.py` (depois dos testes de `/admin/panel/appointments`)

- [ ] **Step 1: Write the failing tests**

Acrescente em `tests/test_webhook.py`, depois de `test_panel_unexpected_exception_returns_502_with_friendly_detail`. Use o mesmo fixture `http_client` e o mesmo jeito de configurar o segredo que `_post_panel` usa (leia a função `_post_panel` e copie as duas linhas de `monkeypatch` que definem o `ADMIN_SECRET`; abaixo elas aparecem como `_set_secret(monkeypatch)`; crie esse helper com essas duas linhas).

```python
# ── POST /admin/panel/appointments/edit e /cancel ─────────────────────────────

@pytest.mark.parametrize("path,handler", [
    ("/admin/panel/appointments/edit", "handle_edit"),
    ("/admin/panel/appointments/cancel", "handle_cancel"),
])
def test_panel_edit_cancel_require_secret(http_client, monkeypatch, path, handler):
    _set_secret(monkeypatch)
    with patch(f"app.panel_appointments.{handler}", new_callable=AsyncMock) as mock_h:
        r = http_client.post(path, json={}, headers={"X-Admin-Secret": "errado"})
    assert r.status_code == 403
    mock_h.assert_not_called()


@pytest.mark.parametrize("path,handler", [
    ("/admin/panel/appointments/edit", "handle_edit"),
    ("/admin/panel/appointments/cancel", "handle_cancel"),
])
def test_panel_edit_cancel_pass_status_and_payload(http_client, monkeypatch, path, handler):
    _set_secret(monkeypatch)
    with patch(f"app.panel_appointments.{handler}", new_callable=AsyncMock,
               return_value=(409, {"detail": {"needs_encaixe": True, "reasons": ["x"]}})):
        r = http_client.post(path, json={"a": 1}, headers={"X-Admin-Secret": "s3cr3t"})
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["x"]


def test_panel_edit_input_error_400(http_client, monkeypatch):
    from app import panel_booking
    _set_secret(monkeypatch)
    with patch("app.panel_appointments.handle_edit", new_callable=AsyncMock,
               side_effect=panel_booking.PanelInputError("nada mudou")):
        r = http_client.post("/admin/panel/appointments/edit", json={}, headers={"X-Admin-Secret": "s3cr3t"})
    assert r.status_code == 400 and r.json()["detail"] == "nada mudou"


def test_panel_cancel_booking_error_502(http_client, monkeypatch):
    from app import panel_booking
    _set_secret(monkeypatch)
    with patch("app.panel_appointments.handle_cancel", new_callable=AsyncMock,
               side_effect=panel_booking.BookingError("não foi possível tirar a consulta da agenda; nada foi alterado")):
        r = http_client.post("/admin/panel/appointments/cancel", json={}, headers={"X-Admin-Secret": "s3cr3t"})
    assert r.status_code == 502 and "nada foi alterado" in r.json()["detail"]


def test_panel_cancel_unexpected_error_friendly_502(http_client, monkeypatch):
    _set_secret(monkeypatch)
    with patch("app.panel_appointments.handle_cancel", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        r = http_client.post("/admin/panel/appointments/cancel", json={}, headers={"X-Admin-Secret": "s3cr3t"})
    assert r.status_code == 502 and "Confira a agenda" in r.json()["detail"]
```

Se o segredo usado nos testes existentes não for `"s3cr3t"`, use o mesmo valor que eles usam.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_webhook.py -q -k "edit or cancel"`
Expected: FAIL (404 nas rotas novas).

- [ ] **Step 3: Implement**

Em `app/main.py`, troque o corpo de `admin_panel_appointments` por uma chamada a um helper comum e acrescente as duas rotas:

```python
async def _run_panel(handler, request: Request):
    """Mapeia os erros dos fluxos do painel: entrada inválida 400, falha de gravação 502,
    qualquer outra coisa 502 com texto amigável (a atendente precisa saber que nada foi feito)."""
    from fastapi.responses import JSONResponse
    from app import panel_booking
    body = await request.json()
    try:
        status, payload = await handler(body)
    except panel_booking.PanelInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except panel_booking.BookingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception:
        logger.exception("painel: falha inesperada em %s", request.url.path)
        return JSONResponse(
            status_code=502,
            content={"detail": "A Eva não conseguiu concluir. Confira a agenda antes de tentar de novo."},
        )
    return JSONResponse(status_code=status, content=payload)


@app.post("/admin/panel/appointments")
async def admin_panel_appointments(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Nova consulta criada pela atendente no painel. Ver app/panel_booking.py."""
    _check_admin_secret(x_admin_secret)
    from app import panel_booking
    return await _run_panel(panel_booking.handle, request)


@app.post("/admin/panel/appointments/edit")
async def admin_panel_appointments_edit(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Alteração de consulta pelo painel. Ver app/panel_appointments.py."""
    _check_admin_secret(x_admin_secret)
    from app import panel_appointments
    return await _run_panel(panel_appointments.handle_edit, request)


@app.post("/admin/panel/appointments/cancel")
async def admin_panel_appointments_cancel(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Cancelamento de consulta pelo painel. Ver app/panel_appointments.py."""
    _check_admin_secret(x_admin_secret)
    from app import panel_appointments
    return await _run_panel(panel_appointments.handle_cancel, request)
```

Atenção: os testes antigos fazem `patch("app.panel_booking.handle", ...)`? Se sim, o `patch` troca o atributo do módulo, e `panel_booking.handle` é lido na hora da chamada, então continua funcionando. Os testes novos fazem `patch("app.panel_appointments.handle_edit")`, lido do mesmo jeito.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_webhook.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_webhook.py
git commit -m "feat(painel): endpoints de alterar e cancelar consulta na Eva"
```

---

### Task 10: `register_payment` respeita cortesia por consulta

Pendência da Parte 2: a consulta marcada como cortesia só pelo painel (`is_courtesy=True`, ficha sem `custom_price=0`) ainda era tratada como consulta paga quando chegava comprovante.

**Files:**
- Modify: `app/graph/tools.py` (`_appt_fields` ~linha 3742 e o trecho do `custom_price` ~linha 3989)
- Test: `tests/test_tools.py` (perto de `test_register_payment_courtesy_zero_price`)

- [ ] **Step 1: Write the failing test**

Em `tests/test_tools.py`, dê a `_make_supabase_client_with_appointment_waived` um parâmetro novo `is_courtesy=False` e ponha `"is_courtesy": is_courtesy` no dict de `apt_data`. Depois acrescente:

```python
async def test_register_payment_courtesy_per_appointment():
    """Cortesia marcada na consulta (painel), com a ficha sem preço zero, também é QUITADA."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment_waived(
        booking_fee_waived=True, custom_price=None, is_courtesy=True
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="0,00",
            drive_link="",
            state=_make_state(preferred_doctor="julio", patient_age=35),
            config=CONFIG,
        )
    assert "QUITADA" in result
    assert "cortesia" in result.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools.py -q -k "courtesy"`
Expected: o teste novo FALHA (sem "QUITADA").

- [ ] **Step 3: Implement**

Em `register_payment`, acrescente `is_courtesy` ao `_appt_fields`:

```python
    _appt_fields = (
        "appointment_id, start_time, end_time, doctor_id, paid_at, "
        "booking_fee_paid_at, status, consultation_type, booking_fee_waived, is_courtesy"
    )
```

Procure dentro de `register_payment` qualquer outro `.select(...)` de `appointments` cujo resultado vira `appt_result` e acrescente `is_courtesy` nele também (rode `grep -n "appt_result =" app/graph/tools.py` para achar). Depois, logo após o bloco que lê `custom_price` da ficha:

```python
    # Cortesia por consulta (painel): vale como preço zero só para esta consulta.
    if appt_result and appt_result.data and appt_result.data[0].get("is_courtesy"):
        custom_price = 0
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "fix(pagamento): cortesia marcada na consulta também quita no register_payment"
```

---

### Task 11: painel: `billing` na lista e rotas de alterar e cancelar

**Files:**
- Modify: `dashboard/attendant_db.py` (`list_consultas`)
- Modify: `dashboard/attendant_routes.py`
- Test: `dashboard/tests/test_attendant_db.py`, `dashboard/tests/test_attendant_routes.py`, `dashboard/tests/test_attendant_scope.py`

- [ ] **Step 1: Write the failing tests**

Em `dashboard/tests/test_attendant_db.py`, acrescente:

```python
async def test_list_consultas_billing(patched_client, fake_client):
    base = {"patient_id": "p1", "status": "scheduled", "doctor_id": JULIO,
            "start_time": "2099-10-05T12:00:00+00:00", "end_time": "2099-10-05T13:00:00+00:00"}
    fake_client.store["appointments"] = [
        {**base, "appointment_id": "a1", "is_courtesy": True, "booking_fee_waived": True},
        {**base, "appointment_id": "a2", "is_courtesy": False, "booking_fee_waived": True},
        {**base, "appointment_id": "a3"},
    ]
    out = await attendant_db.list_consultas("p1")
    assert [a["billing"] for a in out["appointments"]] == ["cortesia", "taxa_isenta", "normal"]
```

Em `dashboard/tests/test_attendant_routes.py`, acrescente:

```python
def test_alterar_forwards_with_appointment_id(client, monkeypatch):
    seen = {}
    async def fake_post(path, body, timeout=None):
        seen["path"], seen["body"], seen["timeout"] = path, body, timeout
        return 200, {"appointment_id": "a1", "warnings": [], "message": {"sent": ["Ana"]}}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    body = {"phone": "5581", "doctor": "julio", "modality": "online", "start": "2026-10-06T09:00",
            "minutes": 60, "initiated_by": "clinic", "agent": "Maria"}
    r = client.post("/api/atendente/consulta/a1/alterar", params={"token": "test-token"}, json=body)
    assert r.status_code == 200
    assert seen["path"] == "/admin/panel/appointments/edit"
    assert seen["body"]["appointment_id"] == "a1" and seen["body"]["initiated_by"] == "clinic"
    assert seen["log"] == "attendant_edit_appointment"
    assert seen["timeout"] == attendant_routes._BOOKING_TIMEOUT


def test_cancelar_forwards_and_logs(client, monkeypatch):
    seen = {}
    async def fake_post(path, body, timeout=None):
        seen["path"], seen["body"] = path, body
        return 200, {"canceled": ["a1"], "warnings": [], "message": {"sent": []}}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/consulta/a1/cancelar", params={"token": "test-token"},
                    json={"phone": "5581", "initiated_by": "patient", "fee_action": "reter"})
    assert r.status_code == 200
    assert seen["path"] == "/admin/panel/appointments/cancel"
    assert seen["body"]["appointment_id"] == "a1" and seen["body"]["fee_action"] == "reter"
    assert seen["log"] == "attendant_cancel_appointment"


def test_cancelar_dry_run_does_not_log(client, monkeypatch):
    seen = {"log": None}
    async def fake_post(path, body, timeout=None):
        seen["timeout"] = timeout
        return 200, {"fee_paid": False, "message": None}
    async def fake_log(t, phone, meta):
        seen["log"] = t
    monkeypatch.setattr(eva_client, "post", fake_post)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/consulta/a1/cancelar", params={"token": "test-token"},
                    json={"phone": "5581", "dry_run": True})
    assert r.status_code == 200 and seen["log"] is None
    assert seen["timeout"] == attendant_routes._DRY_RUN_TIMEOUT


def test_cancelar_eva_timeout_504(client, monkeypatch):
    async def fake_post(path, body, timeout=None):
        raise eva_client.EvaTimeout("timed out")
    monkeypatch.setattr(eva_client, "post", fake_post)
    r = client.post("/api/atendente/consulta/a1/cancelar", params={"token": "test-token"},
                    json={"phone": "5581", "initiated_by": "clinic"})
    assert r.status_code == 504 and "Confira a lista" in r.json()["detail"]
```

Em `dashboard/tests/test_attendant_scope.py`, na seção `# ── consultas`:

```python
@pytest.mark.parametrize("action,body", [
    ("alterar", {"phone": "5581", "doctor": "julio", "modality": "online", "start": "2026-10-06T09:00",
                 "minutes": 60, "initiated_by": "clinic"}),
    ("cancelar", {"phone": "5581", "initiated_by": "clinic"}),
])
def test_alterar_cancelar_out_of_scope(client, monkeypatch, action, body):
    _scope(monkeypatch, "c1", {"p1"})
    async def other(aid):
        return "p9"
    monkeypatch.setattr(attendant_db, "get_appointment_patient_id", other)
    r = client.post(f"/api/atendente/consulta/a1/{action}", params={"token": "test-token"}, json=body)
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py tests/test_attendant_routes.py tests/test_attendant_scope.py -q`
Expected: FAIL (KeyError `billing` e 404 nas rotas novas).

- [ ] **Step 3: Implement**

Em `dashboard/attendant_db.py`, `list_consultas`: acrescente `is_courtesy, booking_fee_waived` ao `select` e, no dict de cada consulta:

```python
            # Só para pré-marcar a cobrança ao alterar; a aba Consultas não mostra pagamento.
            "billing": "cortesia" if r.get("is_courtesy") else "taxa_isenta" if r.get("booking_fee_waived") else "normal",
```

Em `dashboard/attendant_routes.py`, extraia o tratamento de erro de `nova_consulta` para um helper e use-o nas três rotas:

```python
async def _forward_to_eva(path: str, payload: dict, dry_run: bool) -> tuple[int, dict]:
    """Chama a Eva e traduz as falhas para a atendente. Em dry_run nada é gravado,
    então timeout vira "nada foi alterado"; fora dele, a Eva pode ter concluído."""
    timeout = _DRY_RUN_TIMEOUT if dry_run else _BOOKING_TIMEOUT
    try:
        status_code, body = await eva_client.post(path, payload, timeout=timeout)
    except eva_client.EvaTimeout:
        if dry_run:
            raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
        raise HTTPException(
            status_code=504,
            detail="A Eva demorou a responder. Confira a lista de consultas antes de tentar de novo.")
    except eva_client.EvaUnavailable:
        raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
    if status_code in (401, 403):
        raise HTTPException(status_code=503, detail="Painel sem acesso à Eva (configuração). Nada foi alterado.")
    return status_code, body


@router.post("/consulta/nova")
async def nova_consulta(body: NewAppointmentBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, body.patient_id)
    status_code, payload = await _forward_to_eva("/admin/panel/appointments", body.model_dump(), body.dry_run)
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_new_appointment", body.phone, {
            "patient_id": body.patient_id, "agent": body.agent, "encaixe": body.encaixe_confirmed,
            "appointments": [a.get("appointment_id") for a in payload.get("appointments", [])],
        })
    return JSONResponse(status_code=status_code, content=payload)


class EditAppointmentBody(BaseModel):
    phone: str
    doctor: str
    modality: str
    start: str
    minutes: int
    session_note: str = Field(default="", max_length=80)
    first_consultation: bool = False
    billing: str = "normal"
    initiated_by: str | None = None
    encaixe_confirmed: bool = False
    dry_run: bool = False
    agent: str = Field(default="", max_length=80)


class CancelAppointmentBody(BaseModel):
    phone: str
    initiated_by: str | None = None
    fee_action: str | None = None
    reason: str = Field(default="", max_length=200)
    both_parts: bool = False
    dry_run: bool = False
    agent: str = Field(default="", max_length=80)


@router.post("/consulta/{appointment_id}/alterar")
async def alterar_consulta(appointment_id: str, body: EditAppointmentBody, _: None = Depends(verify_token)):
    await _assert_appointment_scope(body.phone, appointment_id)
    payload = {**body.model_dump(), "appointment_id": appointment_id}
    status_code, resp = await _forward_to_eva("/admin/panel/appointments/edit", payload, body.dry_run)
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_edit_appointment", body.phone, {
            "appointment_id": appointment_id, "new_appointment_id": resp.get("appointment_id"),
            "agent": body.agent, "encaixe": body.encaixe_confirmed, "initiated_by": body.initiated_by,
        })
    return JSONResponse(status_code=status_code, content=resp)


@router.post("/consulta/{appointment_id}/cancelar")
async def cancelar_consulta(appointment_id: str, body: CancelAppointmentBody, _: None = Depends(verify_token)):
    await _assert_appointment_scope(body.phone, appointment_id)
    payload = {**body.model_dump(), "appointment_id": appointment_id}
    status_code, resp = await _forward_to_eva("/admin/panel/appointments/cancel", payload, body.dry_run)
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_cancel_appointment", body.phone, {
            "appointment_id": appointment_id, "canceled": resp.get("canceled", []), "agent": body.agent,
            "initiated_by": body.initiated_by, "fee_action": body.fee_action,
        })
    return JSONResponse(status_code=status_code, content=resp)
```

- [ ] **Step 4: Run tests**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS (inclusive os testes antigos de `nova_consulta`).

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/attendant_routes.py dashboard/tests/
git commit -m "feat(painel): rotas de alterar e cancelar consulta"
```

---

### Task 12: tela: ícones no cartão e modo Alterar

A folha "Nova consulta" (`#appt-sheet`) passa a ter dois modos. `APPT.edit` guarda a consulta sendo alterada (ou `null` na criação).

**Files:**
- Modify: `dashboard/templates/atendente.html`

- [ ] **Step 1: CSS e HTML**

No `<style>`, depois de `.eva .btn-info`, acrescente:

```css
  .eva .btn-danger { background: var(--danger); color: var(--on-accent); border-color: var(--danger); }
  .eva .tl-actions { display: flex; gap: 2px; margin-left: auto; }
```

Dentro de `#appt-sheet`, logo antes de `<div id="appt-pending-notice" ...>`, acrescente o "quem pediu":

```html
        <div id="appt-who-wrap" class="hidden flex flex-col gap-2"><span class="cap">Quem pediu a mudança</span>
          <div id="appt-who" class="seg" role="radiogroup" aria-label="Quem pediu a mudança">
            <button type="button" role="radio" data-v="patient">Paciente</button>
            <button type="button" role="radio" data-v="clinic">Clínica</button>
          </div></div>
```

No rodapé da folha, logo antes de `<div id="appt-encaixe" ...>`:

```html
        <div id="appt-late" class="hidden notice notice-warn" role="alert">Remarcação a pedido do paciente com menos de 24h: será cobrada nova taxa.</div>
```

- [ ] **Step 2: ícones no cartão**

Em `apptCard(a)`, troque a primeira linha do cartão (o `div` com hora e meta) por uma versão com os dois ícones à direita:

```js
      <div class="flex items-baseline gap-2 flex-wrap">
        <span class="serif num" style="font-size: 20px">${escapeHtml(a.start_local.slice(11, 16))}</span>
        <span class="text-sm" style="color: var(--muted)">${escapeHtml(meta)}</span>
        <span class="tl-actions">
          <button type="button" class="ibtn" data-edit="${escapeHtml(a.appointment_id)}" aria-label="Alterar consulta">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>
          </button>
          <button type="button" class="ibtn" data-cancel="${escapeHtml(a.appointment_id)}" aria-label="Cancelar consulta">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/></svg>
          </button>
        </span>
      </div>
```

Em `renderConsultas()`, depois do bloco dos interruptores `[data-first]`, ligue os ícones:

```js
  document.querySelectorAll("[data-edit]").forEach((b) => {
    b.onclick = () => openApptEdit(CONSULTAS.appointments.find((x) => x.appointment_id === b.dataset.edit));
  });
  document.querySelectorAll("[data-cancel]").forEach((b) => {
    b.onclick = () => openCancelSheet(CONSULTAS.appointments.find((x) => x.appointment_id === b.dataset.cancel));
  });
```

(`openCancelSheet` nasce na Task 13. Até lá, o clique no ícone de cancelar dá erro no console; tudo bem entre as duas tasks.)

- [ ] **Step 3: estado e helpers do modo Alterar**

1. `const APPT_SEGS` ganha `"appt-who"` no fim.
2. Em `segSet`, aceite `v === null` sem quebrar (já funciona: nenhum botão fica marcado e o primeiro habilitado fica no Tab).
3. Acrescente, antes de `function apptSplit()`:

```js
function apptSplitLabel(note) {
  return (note || "").startsWith(SPLIT_PART1) ? SPLIT_PART1 : (note || "").startsWith(SPLIT_PART2) ? SPLIT_PART2 : "";
}
function apptUrl() {
  return APPT.edit ? `/api/atendente/consulta/${encodeURIComponent(APPT.edit.appointment_id)}/alterar` : "/api/atendente/consulta/nova";
}
```

4. `apptSplit()` passa a começar com `if (APPT.edit) return false;`.
5. `apptBody(extra)`: no começo, antes do código atual:

```js
  if (APPT.edit) {
    return {
      phone: PHONE, doctor: segGet("appt-doctor"), modality: segGet("appt-modality"),
      start: `${$("appt-date").value}T${$("appt-time").value}`, minutes: Number(segGet("appt-dur")),
      session_note: $("appt-note").value.trim(),
      first_consultation: !$("appt-first-wrap").classList.contains("hidden") && $("appt-first").getAttribute("aria-checked") === "true",
      billing: segGet("appt-billing") || "normal", initiated_by: segGet("appt-who"),
      encaixe_confirmed: false, dry_run: false, agent: AGENT, ...extra,
    };
  }
```

- [ ] **Step 4: `apptChanged` no modo Alterar**

Faça estas mudanças em `apptChanged(source)`:

```js
  // Na alteração a observação entra no texto enviado, então ela também refaz a prévia.
  const isNote = source === "appt-note" && !APPT.edit;
```

Logo depois de `$("appt-encaixe").classList.add("hidden");` (dentro do `if (!isNote)`), acrescente `$("appt-late").classList.add("hidden");`.

Troque as linhas da etiqueta e do formato por:

```js
  const splitPart = APPT.edit ? apptSplitLabel(APPT.edit.session_note) : "";
  $("appt-first-wrap").classList.toggle("hidden", !minor || doctor !== "julio" || !!APPT.splitOf || !!splitPart);
  const first = minor && doctor === "julio" && $("appt-first").getAttribute("aria-checked") === "true";
  const canSplit = !APPT.splitOf && !APPT.edit && first && doctor === "julio";
```

A linha da duração vira:

```js
  $("appt-dur-wrap").classList.toggle("hidden", canSplit || !!APPT.splitOf || !!splitPart);
  if (split || APPT.splitOf || splitPart) segSet("appt-dur", "60");
  else if (canSplit) segSet("appt-dur", "120");
```

A conta do `ok` e o texto do botão:

```js
  const ok = !!($("appt-date").value && $("appt-time").value && segGet("appt-modality") && doctor
               && (!APPT.edit || segGet("appt-who")));
  $("appt-submit").disabled = !ok;
  if (!isNote) {
    $("appt-submit").className = "btn btn-primary";
    $("appt-submit").textContent = APPT.edit ? "Salvar alteração" : APPT.splitOf ? "Marcar 2ª parte" : "Agendar";
  }
```

E a prévia automática passa a rodar também na observação quando é alteração: troque `if (source !== "appt-note") APPT_PREVIEW_TIMER = ...` por `if (!isNote) APPT_PREVIEW_TIMER = setTimeout(() => apptPreview(false), 600);`.

- [ ] **Step 5: abrir nos dois modos**

Em `openApptSheet(splitOf)`, na primeira linha depois de `if (!p) return;`, troque `APPT = { splitOf, previewText: null };` por `APPT = { splitOf, previewText: null, edit: null };` e, antes de `apptChanged("open")`, acrescente:

```js
  $("appt-who-wrap").classList.add("hidden");
  $("appt-late").classList.add("hidden");
```

Acrescente a função nova, depois de `openApptSheet`:

```js
function openApptEdit(a) {
  const p = CURRENT_PATIENT;
  if (!p || !a) return;
  openApptSheet(null);            // monta a folha do jeito normal e depois ajusta para alterar
  APPT.edit = a;
  $("appt-cap").textContent = "Alterar consulta";
  $("appt-pending-notice").classList.add("hidden");
  const splitPart = apptSplitLabel(a.session_note);
  segButtons("appt-doctor").forEach((b) => { b.disabled = !!splitPart && b.dataset.v !== "julio"; });
  segSet("appt-doctor", a.doctor_key || "julio");
  if (a.modality) segSet("appt-modality", a.modality);
  $("appt-first").setAttribute("aria-checked", String(a.consultation_type === "primeira_consulta"));
  segSet("appt-dur", String(a.minutes));
  segSet("appt-billing", p.custom_price === 0 ? "cortesia" : (a.billing || "normal"));
  $("appt-billing-wrap").classList.remove("hidden");
  $("appt-date").value = a.start_local.slice(0, 10);
  $("appt-time").value = a.start_local.slice(11, 16);
  $("appt-note").value = (a.session_note || "").replace(SPLIT_PART1, "").replace(SPLIT_PART2, "").replace(/^\s*·\s*/, "").trim();
  segSet("appt-who", null);
  $("appt-who-wrap").classList.remove("hidden");
  apptChanged("open");
  $("appt-who").querySelector("[role=radio]").focus();
}
```

- [ ] **Step 6: prévia, envio e mensagem de resultado**

Em `apptPreview(show)`, troque a URL fixa por `apptUrl()` e, depois de calcular `m`, trate a alteração sem mensagem e o aviso de nova taxa:

```js
    r = await postJson(apptUrl(), apptBody({ dry_run: true }));
  ...
  $("appt-late").classList.toggle("hidden", !body.late_fee);
  if (APPT.edit && body.notify === false) {
    $("appt-to").textContent = "ninguém (a mudança não gera mensagem)";
    APPT.previewText = "";
    $("appt-preview").textContent = "";
    if ((body.encaixe_reasons || []).length && !APPT_ENCAIXE) apptSetEncaixe(body.encaixe_reasons);
    return;
  }
  const m = body.message || { recipients: [], held: [], text: "" };
```

(o `const m = ...` já existe; as linhas acima entram logo antes dele.)

Troque `bookedMessage(m)` por uma função genérica e mantenha o nome antigo chamando a nova, para os textos da criação ficarem iguais:

```js
function resultMessage(m, done, warnings = []) {
  const notDelivered = m.not_delivered || [], held = m.held || [], sent = m.sent || [];
  const out = [];
  if (m.skipped) out.push(`${done} ✓`);
  else {
    if (notDelivered.length) out.push(`${done}, mas a mensagem não foi entregue a ${notDelivered.join(", ")}. Avise o paciente por outro meio.`);
    if (held.length) out.push(`${notDelivered.length ? "" : `${done}. `}Eva desligada para ${held.join(", ")}: não recebeu mensagem.`);
    if (!out.length && !sent.length) out.push(`${done}. Ninguém recebeu a mensagem.`);
  }
  warnings.forEach((w) => out.push(`Atenção: ${w}.`));
  const warn = warnings.length > 0 || (!m.skipped && (notDelivered.length > 0 || held.length > 0 || !sent.length));
  if (!warn && !m.skipped) return { text: `${done} e paciente avisado ✓`, warn: false };
  return { text: out.join(" "), warn };
}
function bookedMessage(m) { return resultMessage(m, "Consulta registrada"); }
```

No `$("appt-submit").onclick`, troque a URL por `apptUrl()`, a mensagem de erro final por `APPT.edit ? "Não foi possível alterar. Nada foi alterado." : "Não foi possível agendar. Nada foi alterado."` e o final por:

```js
  const isEdit = !!APPT.edit;
  closeApptSheet();
  loadConsultas(CURRENT_PID);
  loadPagamentos();
  const { text, warn } = isEdit
    ? resultMessage(body.message || {}, "Alteração salva", body.warnings || [])
    : bookedMessage(body.message || {});
  if (warn) flash(text, 10000, "warn");
  else flash(text, 3000);
```

- [ ] **Step 7: Rodar os testes do painel**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS (o template é renderizado em testes de rota; nenhum teste quebra).

- [ ] **Step 8: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(painel): alterar consulta pela folha lateral"
```

---

### Task 13: tela: folha Cancelar

**Files:**
- Modify: `dashboard/templates/atendente.html`

- [ ] **Step 1: HTML**

Logo depois do fechamento de `#appt-sheet` (`</div>` que fecha `<div id="appt-sheet" class="hidden">`), acrescente:

```html
  <div id="cancel-sheet" class="hidden">
    <div class="scrim" data-close-cancel></div>
    <aside class="sheet" role="dialog" aria-modal="true" aria-labelledby="cancel-title">
      <div class="flex items-start gap-3" style="padding: 24px 28px 18px; border-bottom: 1px solid var(--soft)">
        <div class="flex-1 flex flex-col gap-1" style="min-width: 0">
          <span class="cap">Cancelar consulta</span>
          <h2 id="cancel-title" class="serif" style="font-size: 26px; line-height: 1.15; margin: 0"></h2>
          <span id="cancel-when" class="text-sm num" style="color: var(--muted)"></span>
        </div>
        <button type="button" class="ibtn" data-close-cancel aria-label="Fechar">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>
        </button>
      </div>
      <div class="flex-1 overflow-auto flex flex-col gap-5" style="padding: 22px 28px">
        <div class="flex flex-col gap-2"><span class="cap">Quem pediu</span>
          <div id="cancel-who" class="seg" role="radiogroup" aria-label="Quem pediu o cancelamento">
            <button type="button" role="radio" data-v="patient">Paciente</button>
            <button type="button" role="radio" data-v="clinic">Clínica</button>
          </div></div>
        <div id="cancel-fee-wrap" class="hidden flex flex-col gap-2"><span class="cap">Taxa de reserva paga</span>
          <div id="cancel-fee" class="seg" role="radiogroup" aria-label="O que fazer com a taxa">
            <button type="button" role="radio" data-v="devolver">Devolver</button>
            <button type="button" role="radio" data-v="credito">Guardar para remarcar</button>
            <button type="button" role="radio" data-v="reter">Reter</button>
          </div>
          <div id="cancel-policy" class="notice"></div>
        </div>
        <div id="cancel-both-wrap" class="hidden flex flex-col gap-2"><span class="cap">1ª consulta dividida</span>
          <div id="cancel-both" class="seg" role="radiogroup" aria-label="Quais partes cancelar">
            <button type="button" role="radio" data-v="so">Só esta parte</button>
            <button type="button" role="radio" data-v="ambas">As duas partes</button>
          </div>
          <span id="cancel-sibling" class="text-sm" style="color: var(--muted)"></span>
        </div>
        <label class="flex flex-col gap-2"><span id="cancel-reason-cap" class="cap">Motivo (opcional)</span>
          <textarea id="cancel-reason" class="inp" rows="2" maxlength="200" placeholder="ex.: exceção aprovada pelo Dr. Júlio"></textarea></label>
      </div>
      <div class="flex flex-col gap-3" style="padding: 12px 28px 20px; border-top: 1px solid var(--soft)">
        <p id="cancel-error" class="hidden text-sm" style="color: var(--danger); margin: 0" role="alert"></p>
        <div class="flex items-center gap-2 text-sm flex-wrap">
          <span style="color: var(--muted)">Mensagem para</span>
          <span id="cancel-to" class="flex-1 font-medium num" style="min-width: 0">—</span>
          <button id="cancel-preview-btn" type="button" class="link" aria-expanded="false" aria-controls="cancel-preview">ver mensagem</button>
        </div>
        <pre id="cancel-preview" class="hidden text-sm card" style="white-space: pre-wrap; font: inherit; padding: 12px 14px; margin: 0; max-height: 200px; overflow: auto"></pre>
        <div class="flex justify-end">
          <button id="cancel-submit" type="button" class="btn btn-danger" disabled>Cancelar consulta</button>
        </div>
      </div>
    </aside>
  </div>
```

Se a classe `.inp` não servir para `textarea` (altura fixa), acrescente no `<style>`: `.eva textarea.inp { height: auto; min-height: 64px; padding-top: 10px; resize: vertical; }`.

- [ ] **Step 2: JS**

Antes de `initPhone();` no fim do script, acrescente:

```js
// ── Cancelar ───────────────────────────────────────────────────────────────
let CANCEL = null;               // { a, info, previewText }
let CANCEL_SEQ = 0;
let CANCEL_TIMER = null;
let CANCEL_RETURN_FOCUS = null;

function wireSeg(id, onChange) {
  segButtons(id).forEach((b) => {
    b.onclick = () => { if (!b.disabled) { segSet(id, b.dataset.v); onChange(id); } };
  });
  $(id).addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const bs = segButtons(id).filter((b) => !b.disabled);
    const next = bs[(bs.indexOf(document.activeElement) + step + bs.length) % bs.length];
    if (next) { segSet(id, next.dataset.v); next.focus(); onChange(id); }
  });
}
["cancel-who", "cancel-fee", "cancel-both"].forEach((id) => wireSeg(id, cancelChanged));
$("cancel-reason").oninput = () => cancelChanged("cancel-reason");

function cancelBody(extra = {}) {
  const info = CANCEL.info || {};
  return {
    phone: PHONE, initiated_by: segGet("cancel-who"),
    fee_action: info.fee_paid ? segGet("cancel-fee") : null,
    reason: $("cancel-reason").value.trim(), both_parts: segGet("cancel-both") === "ambas",
    dry_run: false, agent: AGENT, ...extra,
  };
}
function cancelUrl() { return `/api/atendente/consulta/${encodeURIComponent(CANCEL.a.appointment_id)}/cancelar`; }
function cancelError(msg) { $("cancel-error").textContent = msg; $("cancel-error").classList.remove("hidden"); }

function cancelReasonRequired() {
  const info = CANCEL.info || {};
  return !!(info.fee_paid && info.policy_late && segGet("cancel-fee") === "devolver");
}

function cancelValidate() {
  const info = CANCEL.info;
  const need = cancelReasonRequired();
  $("cancel-reason-cap").textContent = need ? "Motivo (obrigatório)" : "Motivo (opcional)";
  const ok = !!info && !!segGet("cancel-who")
    && (!info.fee_paid || !!segGet("cancel-fee"))
    && (!need || !!$("cancel-reason").value.trim());
  $("cancel-submit").disabled = !ok;
}

function renderCancelInfo(info) {
  $("cancel-fee-wrap").classList.toggle("hidden", !info.fee_paid);
  segDisable("cancel-fee", "credito", info.status === "pending_reschedule");
  if (info.status === "pending_reschedule" && segGet("cancel-fee") === "credito") segSet("cancel-fee", null);
  const pol = $("cancel-policy");
  pol.className = `notice ${info.policy_late ? "notice-warn" : ""}`;
  pol.textContent = info.policy_late
    ? "Pela política, com menos de 24h a taxa é retida. Exceções são avaliadas pela equipe ou pelos médicos."
    : "Pela política, com 24h ou mais de antecedência a taxa não é retida.";
  $("cancel-both-wrap").classList.toggle("hidden", !info.sibling);
  $("cancel-sibling").textContent = info.sibling ? `Outra parte: ${info.sibling.line}` : "";
  const m = info.message;
  if (!m) { $("cancel-to").textContent = "escolha quem pediu"; CANCEL.previewText = null; return; }
  const held = m.held || [];
  $("cancel-to").textContent = (m.recipients || []).length
    ? m.recipients.map((c) => `${c.name} · final ${c.phone_hint}`).join(", ")
    : "ninguém (sem número liberado)";
  CANCEL.previewText = (m.text || "") + (held.length ? `\n\n(Eva desligada para ${held.join(", ")}: não recebe.)` : "");
  $("cancel-preview").textContent = CANCEL.previewText;
}

async function cancelInfo() {
  const seq = ++CANCEL_SEQ;
  let r, body;
  try {
    r = await postJson(cancelUrl(), cancelBody({ dry_run: true }));
    body = await r.json().catch(() => ({}));
  } catch (_) { r = null; body = {}; }
  if (seq !== CANCEL_SEQ || $("cancel-sheet").classList.contains("hidden")) return;
  if (!r || !r.ok) { cancelError(errText(body, "Não foi possível ler a consulta.")); return; }
  CANCEL.info = body;
  renderCancelInfo(body);
  cancelValidate();
}

function cancelChanged(source) {
  $("cancel-error").classList.add("hidden");
  cancelValidate();
  if (source === "cancel-reason") return;   // o motivo não muda o texto enviado
  CANCEL_SEQ++;
  clearTimeout(CANCEL_TIMER);
  $("cancel-preview").classList.add("hidden");
  $("cancel-preview-btn").setAttribute("aria-expanded", "false");
  CANCEL_TIMER = setTimeout(cancelInfo, 300);
}

function openCancelSheet(a) {
  const p = CURRENT_PATIENT;
  if (!p || !a) return;
  CANCEL = { a, info: null, previewText: null };
  $("cancel-title").textContent = p.name || "";
  $("cancel-when").textContent = `${a.start_local.slice(8, 10)}/${a.start_local.slice(5, 7)} às ${a.start_local.slice(11, 16)} · ${DOCTOR_LABEL[a.doctor_key] || ""}`;
  ["cancel-who", "cancel-fee"].forEach((id) => segSet(id, null));
  segSet("cancel-both", "so");
  $("cancel-reason").value = "";
  $("cancel-fee-wrap").classList.add("hidden");
  $("cancel-both-wrap").classList.add("hidden");
  $("cancel-error").classList.add("hidden");
  $("cancel-preview").classList.add("hidden");
  $("cancel-to").textContent = "—";
  $("cancel-submit").disabled = true;
  CANCEL_RETURN_FOCUS = document.activeElement;
  document.querySelector(".eva > .max-w-5xl").inert = true;
  $("cancel-sheet").classList.remove("hidden");
  $("cancel-who").querySelector("[role=radio]").focus();
  cancelInfo();
}
function closeCancelSheet() {
  CANCEL_SEQ++;
  clearTimeout(CANCEL_TIMER);
  $("cancel-sheet").classList.add("hidden");
  document.querySelector(".eva > .max-w-5xl").inert = false;
  if (CANCEL_RETURN_FOCUS && document.body.contains(CANCEL_RETURN_FOCUS)) CANCEL_RETURN_FOCUS.focus();
  CANCEL_RETURN_FOCUS = null;
}
document.querySelectorAll("[data-close-cancel]").forEach((el) => { el.onclick = closeCancelSheet; });

$("cancel-preview-btn").onclick = () => {
  const open = !$("cancel-preview").classList.contains("hidden");
  if (open || CANCEL.previewText === null) {
    $("cancel-preview").classList.add("hidden");
    $("cancel-preview-btn").setAttribute("aria-expanded", "false");
    if (!open && CANCEL.previewText === null) cancelError("Escolha quem pediu para ver a mensagem.");
    return;
  }
  $("cancel-preview").classList.remove("hidden");
  $("cancel-preview-btn").setAttribute("aria-expanded", "true");
};

$("cancel-submit").onclick = () => withBusy($("cancel-submit"), async () => {
  $("cancel-error").classList.add("hidden");
  CANCEL_SEQ++;
  clearTimeout(CANCEL_TIMER);
  let r, body;
  try {
    r = await postJson(cancelUrl(), cancelBody());
    body = await r.json().catch(() => ({}));
  } catch (_) {
    cancelError("Sem conexão com o painel. Confira a lista antes de tentar de novo.");
    return;
  }
  if (r.status === 504) {
    cancelError(errText(body, "A Eva demorou a responder. Confira a lista de consultas antes de tentar de novo."));
    loadConsultas(CURRENT_PID);
    return;
  }
  if (!r.ok) { cancelError(errText(body, "Não foi possível cancelar. Nada foi alterado.")); return; }
  closeCancelSheet();
  loadConsultas(CURRENT_PID);
  loadPagamentos();
  const { text, warn } = resultMessage(body.message || {}, "Consulta cancelada", body.warnings || []);
  if (warn) flash(text, 10000, "warn");
  else flash(text, 3000);
});
```

No handler global de `Escape` (procure `if (e.key !== "Escape") return;`), acrescente como primeira checagem:

```js
  if (!$("cancel-sheet").classList.contains("hidden")) { closeCancelSheet(); return; }
```

- [ ] **Step 3: Rodar os testes do painel**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(painel): cancelar consulta pela folha lateral"
```

---

### Task 14: verificação final

**Files:** nenhum novo, fora o servidor de prévia no scratchpad.

- [ ] **Step 1: Suíte completa da Eva**

Run: `uv run pytest --tb=short -q`
Expected: tudo passa (Parte 2 terminou com 1414; agora mais os testes novos).

- [ ] **Step 2: Suíte completa do painel**

Run: `cd dashboard && uv run pytest --tb=short -q`
Expected: tudo passa (Parte 2 terminou com 283; agora mais os novos).

- [ ] **Step 3: Prévia visual com banco falso**

A sessão da Parte 2 deixou um servidor de prévia com banco falso no scratchpad (`preview_painel2.py`, porta 8765, com `eva_client.post` falso). Copie-o para `preview_painel3.py` e faça o `eva_client.post` falso responder também a `/admin/panel/appointments/edit` (dry_run devolve `{"encaixe_reasons": [], "late_fee": true, "notify": true, "message": {...}}`; real devolve `{"appointment_id": "a1", "warnings": [], "message": {"sent": ["Carla"]}}`) e a `/admin/panel/appointments/cancel` (dry_run devolve `{"fee_paid": true, "status": "scheduled", "hours_until": 10, "policy_late": true, "sibling": null, "message": ...}` quando `initiated_by` vem preenchido, e `message: null` quando não; real devolve `{"canceled": ["a1"], "warnings": [], "message": {"sent": ["Carla"]}}`). Suba com `preview_start`, abra `/atendente` e confira, em 1280px e em 375px: ícones de alterar e cancelar no cartão; folha Alterar com "Quem pediu" sem nada marcado e botão desligado até escolher; aviso âmbar de nova taxa; folha Cancelar com política âmbar, motivo que vira obrigatório ao escolher Devolver, botão vermelho, Escape fechando. Se o arquivo de prévia não existir mais, crie um mínimo que monte `FastAPI` com `attendant_routes.router`, troque `attendant_db.scope_for_phone`, `list_consultas`, `resolve` e `eva_client.post` por funções falsas e sirva o template.

- [ ] **Step 4: Commit final se algo mudou na verificação**

```bash
git status --short
```

Expected: limpo (o arquivo de prévia fica no scratchpad, fora do repositório).

---

## Depois do merge (para a Ayexa, não para o executor)

Nenhuma migração nova. Nenhuma variável de ambiente nova (o painel já tem `EVA_BASE_URL` e `ADMIN_SECRET` desde a Parte 2). Teste em produção com uma ficha de teste: alterar só a observação (não manda mensagem), alterar o horário a pedido da clínica (manda "precisou alterar"), cancelar com taxa não paga, e conferir Calendar, `appointments`, `events` e `messages`.
