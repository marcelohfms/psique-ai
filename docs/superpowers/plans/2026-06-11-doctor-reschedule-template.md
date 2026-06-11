# Doctor Reschedule Template — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que a clínica envie manualmente um template WhatsApp de reagendamento para pacientes e que o bot processe a resposta livre do paciente automaticamente.

**Architecture:** Um script CLI dispara o template Meta e salva `pending_reschedule` (JSON) no Supabase. O campo é carregado no `ConversationState` do LangGraph. O `patient_agent_node` injeta o contexto no system prompt para que o LLM interprete a intenção do paciente (confirmar, recusar, ou perguntar) e aja accordingly.

**Tech Stack:** Python, Supabase (AsyncClient), `app/whatsapp.py:send_template`, LangGraph (`ConversationState`), `app/graph/nodes.py:patient_agent_node`

---

## Mapa de arquivos

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `supabase/migrations/YYYYMMDD_add_pending_reschedule.sql` | Criar | Migration SQL que adiciona coluna `pending_reschedule JSONB` na tabela `users` |
| `scripts/reschedule_notify.py` | Criar | CLI que busca agendamento, envia template e grava `pending_reschedule` |
| `app/graph/state.py` | Modificar | Adicionar campo `pending_reschedule: dict \| None` ao `ConversationState` |
| `app/graph/nodes.py` | Modificar | Carregar `pending_reschedule` do DB no `collect_info_node`; injetar no system prompt do `patient_agent_node`; limpar após resolução |
| `tests/test_reschedule_notify.py` | Criar | Testes unitários do script de disparo |
| `tests/test_process_message.py` | Modificar | Testes de integração do fluxo de resposta do paciente |

---

## Task 1: Migration SQL — coluna `pending_reschedule`

**Files:**
- Create: `supabase/migrations/20260611000000_add_pending_reschedule.sql`

- [ ] **Step 1: Verificar migrations existentes**

```bash
ls supabase/migrations/
```

- [ ] **Step 2: Criar o arquivo de migration**

```sql
-- supabase/migrations/20260611000000_add_pending_reschedule.sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS pending_reschedule JSONB DEFAULT NULL;
```

- [ ] **Step 3: Aplicar no Supabase (painel ou CLI)**

No painel do Supabase: SQL Editor → cole o conteúdo acima → Run.

Ou via Supabase CLI:
```bash
supabase db push
```

- [ ] **Step 4: Verificar coluna criada**

No painel Supabase, tabela `users`, confirmar que coluna `pending_reschedule` aparece com tipo `jsonb`.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260611000000_add_pending_reschedule.sql
git commit -m "chore: adicionar coluna pending_reschedule na tabela users"
```

---

## Task 2: Adicionar `pending_reschedule` ao `ConversationState`

**Files:**
- Modify: `app/graph/state.py`

- [ ] **Step 1: Abrir `app/graph/state.py` e localizar o final da classe**

O arquivo termina com o campo `pending_appointment: dict | None`.

- [ ] **Step 2: Adicionar o novo campo**

Após a linha de `pending_appointment`, adicionar:

```python
    # Holds a pending reschedule offer sent to the patient via Meta template.
    # Set by scripts/reschedule_notify.py. Keys: appointment_id, suggested_start, suggested_end.
    # Cleared once the patient confirms or declines.
    pending_reschedule: dict | None
```

- [ ] **Step 3: Rodar testes para verificar que nada quebrou**

```bash
uv run pytest --tb=short -q
```

Expected: todos os testes passam (o novo campo é opcional e não afeta testes existentes).

- [ ] **Step 4: Commit**

```bash
git add app/graph/state.py
git commit -m "feat: adicionar pending_reschedule ao ConversationState"
```

---

## Task 3: Script de disparo `scripts/reschedule_notify.py`

**Files:**
- Create: `scripts/reschedule_notify.py`
- Create: `tests/test_reschedule_notify.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_reschedule_notify.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")


@pytest.mark.asyncio
async def test_format_template_variables():
    """_format_template_vars retorna as 6 variáveis corretas."""
    from scripts.reschedule_notify import _format_template_vars

    original_start = datetime(2026, 6, 13, 10, 0, tzinfo=TZ)
    suggested_start = datetime(2026, 6, 16, 14, 0, tzinfo=TZ)

    result = _format_template_vars(
        patient_name="Ana",
        doctor_label="Dr. Júlio",
        original_start=original_start,
        suggested_start=suggested_start,
    )

    assert result["patient_name"] == "Ana"
    assert result["doctor_label"] == "Dr. Júlio"
    assert "13/06" in result["original_date"]
    assert result["original_time"] == "10h"
    assert "16/06" in result["suggested_date"]
    assert result["suggested_time"] == "14h"


@pytest.mark.asyncio
async def test_send_reschedule_template_calls_send_template():
    """send_reschedule_template chama send_template com as variáveis corretas."""
    from scripts.reschedule_notify import send_reschedule_template

    mock_send = AsyncMock()
    with patch("scripts.reschedule_notify.send_template", mock_send):
        await send_reschedule_template(
            phone="5583999999999",
            patient_name="Ana",
            doctor_label="Dr. Júlio",
            original_date="sexta, 13/06",
            original_time="10h",
            suggested_date="segunda, 16/06",
            suggested_time="14h",
        )

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert call_kwargs[0][0] == "5583999999999"
    assert call_kwargs[0][1] == "psique_reagendamento"
    assert call_kwargs[0][2] == "pt_BR"
    components = call_kwargs[0][3]
    params = components[0]["parameters"]
    assert params[0]["text"] == "Ana"
    assert params[1]["text"] == "Dr. Júlio"
    assert params[2]["text"] == "sexta, 13/06"
    assert params[3]["text"] == "10h"
    assert params[4]["text"] == "segunda, 16/06"
    assert params[5]["text"] == "14h"
```

- [ ] **Step 2: Rodar o teste para verificar que falha**

```bash
uv run pytest tests/test_reschedule_notify.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.reschedule_notify'`

- [ ] **Step 3: Implementar `scripts/reschedule_notify.py`**

```python
"""
Script de disparo manual do template de reagendamento.

Uso:
  uv run python scripts/reschedule_notify.py \
    --phone 5583999999999 \
    --appointment-id <uuid> \
    --new-start 2026-06-16T14:00:00-03:00 \
    --new-end   2026-06-16T15:00:00-03:00
"""
import argparse
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")

DOCTOR_LABELS = {
    "julio": "Dr. Júlio",
    "bruna": "Dra. Bruna",
}

_WEEKDAYS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _format_template_vars(
    patient_name: str,
    doctor_label: str,
    original_start: datetime,
    suggested_start: datetime,
) -> dict:
    def _fmt_date(dt: datetime) -> str:
        day_name = _WEEKDAYS_PT[dt.weekday()]
        return f"{day_name}, {dt.strftime('%d/%m')}"

    def _fmt_time(dt: datetime) -> str:
        if dt.minute == 0:
            return f"{dt.hour}h"
        return dt.strftime("%Hh%M")

    return {
        "patient_name": patient_name,
        "doctor_label": doctor_label,
        "original_date": _fmt_date(original_start),
        "original_time": _fmt_time(original_start),
        "suggested_date": _fmt_date(suggested_start),
        "suggested_time": _fmt_time(suggested_start),
    }


async def send_reschedule_template(
    phone: str,
    patient_name: str,
    doctor_label: str,
    original_date: str,
    original_time: str,
    suggested_date: str,
    suggested_time: str,
) -> None:
    from app.whatsapp import send_template

    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": patient_name},
                {"type": "text", "text": doctor_label},
                {"type": "text", "text": original_date},
                {"type": "text", "text": original_time},
                {"type": "text", "text": suggested_date},
                {"type": "text", "text": suggested_time},
            ],
        }
    ]
    await send_template(phone, "psique_reagendamento", "pt_BR", components)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Envia template de reagendamento ao paciente.")
    parser.add_argument("--phone", required=True, help="Telefone internacional sem +")
    parser.add_argument("--appointment-id", required=True, dest="appointment_id")
    parser.add_argument("--new-start", required=True, dest="new_start", help="ISO 8601 com fuso")
    parser.add_argument("--new-end", required=True, dest="new_end", help="ISO 8601 com fuso")
    args = parser.parse_args()

    from app.database import get_supabase, DOCTOR_NAMES

    db = await get_supabase()

    appt_result = await (
        db.from_("appointments")
        .select("user_id, start_time, doctor_id")
        .eq("appointment_id", args.appointment_id)
        .single()
        .execute()
    )
    appt = appt_result.data
    if not appt:
        raise SystemExit(f"Agendamento não encontrado: {args.appointment_id}")

    user_result = await (
        db.from_("users")
        .select("name, patient_name")
        .eq("id", appt["user_id"])
        .single()
        .execute()
    )
    user = user_result.data
    if not user:
        raise SystemExit(f"Usuário não encontrado para o agendamento {args.appointment_id}")

    patient_name = user.get("patient_name") or user.get("name") or "Paciente"
    doctor_key = DOCTOR_NAMES.get(appt.get("doctor_id", ""), "")
    doctor_label = DOCTOR_LABELS.get(doctor_key, "médico(a)")

    original_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    suggested_start = datetime.fromisoformat(args.new_start).astimezone(TZ)
    suggested_end = datetime.fromisoformat(args.new_end).astimezone(TZ)

    vars_ = _format_template_vars(patient_name, doctor_label, original_start, suggested_start)

    await send_reschedule_template(
        phone=args.phone,
        patient_name=vars_["patient_name"],
        doctor_label=vars_["doctor_label"],
        original_date=vars_["original_date"],
        original_time=vars_["original_time"],
        suggested_date=vars_["suggested_date"],
        suggested_time=vars_["suggested_time"],
    )

    await db.from_("users").update({
        "pending_reschedule": {
            "appointment_id": args.appointment_id,
            "suggested_start": suggested_start.isoformat(),
            "suggested_end": suggested_end.isoformat(),
        }
    }).eq("id", appt["user_id"]).execute()

    print(f"✅ Template enviado para {args.phone}. pending_reschedule gravado.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Rodar os testes**

```bash
uv run pytest tests/test_reschedule_notify.py -v
```

Expected: 2 testes PASS.

- [ ] **Step 5: Rodar suite completa**

```bash
uv run pytest --tb=short -q
```

Expected: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add scripts/reschedule_notify.py tests/test_reschedule_notify.py
git commit -m "feat: script de disparo do template de reagendamento"
```

---

## Task 4: Carregar `pending_reschedule` do DB no `collect_info_node`

**Files:**
- Modify: `app/graph/nodes.py` (bloco de carregamento do usuário, ~linha 130-152)

- [ ] **Step 1: Localizar o bloco onde os campos do usuário são carregados**

No `collect_info_node`, o bloco em torno da linha 131 monta o dict `loaded` com campos do usuário. Adicionar `pending_reschedule` nesse carregamento:

```python
# Dentro do bloco `loaded = { ... }` (aprox. linha 131-147):
loaded = {
    "user_db_id": u["id"],
    "user_name": u.get("name"),
    "patient_name": u.get("patient_name") or u.get("name"),
    "patient_age": u.get("age"),
    "birth_date": u.get("birth_date"),
    "is_patient": u.get("is_patient"),
    "is_returning_patient": u.get("is_returning_patient"),
    "preferred_doctor": doc_key,
    "patient_email": u.get("email"),
    "guardian_name": u.get("guardian_name"),
    "guardian_cpf": u.get("guardian_cpf"),
    "guardian_relationship": u.get("guardian_relationship"),
    "patient_cpf": u.get("patient_cpf"),
    "modality_restriction": u.get("modality_restriction"),
    "age_exception": u.get("age_exception"),
    "pending_reschedule": u.get("pending_reschedule"),   # ← adicionar esta linha
}
```

- [ ] **Step 2: Rodar os testes**

```bash
uv run pytest --tb=short -q
```

Expected: todos os testes passam.

- [ ] **Step 3: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat: carregar pending_reschedule do DB no collect_info_node"
```

---

## Task 5: Injetar `pending_reschedule` no system prompt do `patient_agent_node`

**Files:**
- Modify: `app/graph/nodes.py` (bloco de construção do system prompt, ~linha 951-960)

- [ ] **Step 1: Escrever o teste que falha**

Em `tests/test_process_message.py`, adicionar:

```python
@pytest.mark.asyncio
async def test_pending_reschedule_injected_in_system_prompt(monkeypatch):
    """Quando pending_reschedule está no estado, o system prompt menciona o horário sugerido."""
    import app.graph.nodes as nodes_mod

    captured_prompt = {}

    original_create = nodes_mod.client.chat.completions.create

    async def mock_create(**kwargs):
        system_msgs = [m["content"] for m in kwargs.get("messages", []) if m["role"] == "system"]
        captured_prompt["system"] = " ".join(system_msgs)
        # Return minimal valid response
        from unittest.mock import MagicMock
        msg = MagicMock()
        msg.content = "Ótimo, agendarei para segunda!"
        msg.tool_calls = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    monkeypatch.setattr(nodes_mod.client.chat.completions, "create", mock_create)

    state = {
        "phone": "5583999999999",
        "stage": "patient_agent",
        "messages": [HumanMessage(content="sim, pode ser")],
        "user_name": "Ana",
        "patient_name": "Ana",
        "preferred_doctor": "julio",
        "pending_reschedule": {
            "appointment_id": "abc-123",
            "suggested_start": "2026-06-16T14:00:00-03:00",
            "suggested_end": "2026-06-16T15:00:00-03:00",
        },
        # other required fields
        "is_patient": True,
        "is_returning_patient": True,
        "patient_age": 30,
        "silent_mode": False,
        "pending_patients": None,
        "pending_confirmation_patient": None,
        "user_db_id": "user-uuid",
        "modality_restriction": None,
        "age_exception": None,
        "pending_action": None,
        "pending_appointment": None,
        "birth_date": None,
        "guardian_relationship": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "patient_email": "ana@email.com",
        "patient_cpf": None,
        "consultation_reason": None,
        "referral_professional": None,
        "medication_note": None,
    }

    await nodes_mod.patient_agent_node(state, config={})

    assert "pending_reschedule" in captured_prompt["system"] or "reagendamento" in captured_prompt["system"].lower()
    assert "2026-06-16" in captured_prompt["system"]
```

- [ ] **Step 2: Rodar o teste para verificar que falha**

```bash
uv run pytest tests/test_process_message.py::test_pending_reschedule_injected_in_system_prompt -v
```

Expected: FAIL (assert falha — system prompt não menciona pending_reschedule ainda).

- [ ] **Step 3: Adicionar bloco no system prompt do `patient_agent_node`**

Após o bloco existente de `pending_appointment` (~linha 960), adicionar:

```python
        _pending_reschedule = state.get("pending_reschedule")
        if _pending_reschedule:
            import json as _json
            system_prompt += (
                f"\n\nREAGENDAMENTO PENDENTE: A clínica enviou ao paciente um template sugerindo "
                f"o horário {_pending_reschedule.get('suggested_start')} como nova data para a consulta "
                f"(ID do agendamento original: {_pending_reschedule.get('appointment_id')}). "
                "O paciente acabou de responder. Interprete livremente a intenção:\n"
                "- Se o paciente confirmar (ex: 'sim', 'pode ser', 'tudo bem', 'combinado'): "
                "chame reschedule_appointment com appointment_id e new_slot_datetime = suggested_start.\n"
                "- Se o paciente recusar ou querer outro horário: responda acolhedoramente e entre no fluxo "
                "normal de escolha de horário. O campo pending_reschedule será limpo automaticamente.\n"
                "- Se o paciente tiver dúvidas: responda e mantenha o contexto de reagendamento."
            )
```

- [ ] **Step 4: Rodar o teste**

```bash
uv run pytest tests/test_process_message.py::test_pending_reschedule_injected_in_system_prompt -v
```

Expected: PASS.

- [ ] **Step 5: Rodar suite completa**

```bash
uv run pytest --tb=short -q
```

Expected: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat: injetar pending_reschedule no system prompt do patient_agent_node"
```

---

## Task 6: Limpar `pending_reschedule` após resolução

**Files:**
- Modify: `app/graph/nodes.py` (bloco de retorno do `patient_agent_node`, ~linha 1280-1310)

Quando o LLM chama `reschedule_appointment` ou quando o paciente recusa, o campo deve ser zerado no Supabase e no estado.

- [ ] **Step 1: Escrever o teste que falha**

Em `tests/test_process_message.py`, adicionar:

```python
@pytest.mark.asyncio
async def test_pending_reschedule_cleared_after_reschedule_tool_call(monkeypatch):
    """Após reschedule_appointment ser chamado, pending_reschedule deve ser None no retorno."""
    import app.graph.nodes as nodes_mod

    # Mock the LLM to return a tool call for reschedule_appointment
    async def mock_create(**kwargs):
        from unittest.mock import MagicMock
        tool_call = MagicMock()
        tool_call.function.name = "reschedule_appointment"
        tool_call.function.arguments = '{"appointment_id": "abc-123", "new_slot_datetime": "2026-06-16T14:00:00", "slot_duration_minutes": 60}'
        tool_call.id = "call_1"
        msg = MagicMock()
        msg.content = None
        msg.tool_calls = [tool_call]
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    monkeypatch.setattr(nodes_mod.client.chat.completions, "create", mock_create)

    mock_reschedule = AsyncMock(return_value="Consulta remarcada com sucesso. ✅")
    monkeypatch.setattr(nodes_mod, "reschedule_appointment", mock_reschedule)

    state = {
        "phone": "5583999999999",
        "stage": "patient_agent",
        "messages": [HumanMessage(content="sim, pode ser")],
        "user_name": "Ana",
        "patient_name": "Ana",
        "preferred_doctor": "julio",
        "pending_reschedule": {
            "appointment_id": "abc-123",
            "suggested_start": "2026-06-16T14:00:00-03:00",
            "suggested_end": "2026-06-16T15:00:00-03:00",
        },
        "is_patient": True,
        "is_returning_patient": True,
        "patient_age": 30,
        "silent_mode": False,
        "pending_patients": None,
        "pending_confirmation_patient": None,
        "user_db_id": "user-uuid",
        "modality_restriction": None,
        "age_exception": None,
        "pending_action": None,
        "pending_appointment": None,
        "birth_date": None,
        "guardian_relationship": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "patient_email": "ana@email.com",
        "patient_cpf": None,
        "consultation_reason": None,
        "referral_professional": None,
        "medication_note": None,
    }

    result = await nodes_mod.patient_agent_node(state, config={})

    assert result.get("pending_reschedule") is None
```

- [ ] **Step 2: Rodar o teste para verificar que falha**

```bash
uv run pytest tests/test_process_message.py::test_pending_reschedule_cleared_after_reschedule_tool_call -v
```

Expected: FAIL.

- [ ] **Step 3: Adicionar limpeza do `pending_reschedule` no retorno do `patient_agent_node`**

No final do `patient_agent_node`, no bloco que constrói o `update` dict de retorno, adicionar:

```python
        # Limpar pending_reschedule quando reschedule_appointment foi chamado
        if state.get("pending_reschedule"):
            tool_names_called = [
                tc.function.name
                for tc in (response.tool_calls or [])
                if hasattr(tc, "function")
            ]
            if "reschedule_appointment" in tool_names_called:
                update["pending_reschedule"] = None
                if state.get("user_db_id"):
                    await upsert_user(
                        state["phone"],
                        {"pending_reschedule": None},
                        user_id=state["user_db_id"],
                    )
```

Observação: para o caso de recusa, o LLM não chama `reschedule_appointment` — o `pending_reschedule` permanece até a próxima rodada onde o bot entra no fluxo de escolha de horário. Adicionar também limpeza ao entrar no fluxo normal de agendamento (quando `get_available_slots` é chamado):

```python
        if state.get("pending_reschedule"):
            if "get_available_slots" in tool_names_called:
                update["pending_reschedule"] = None
                if state.get("user_db_id"):
                    await upsert_user(
                        state["phone"],
                        {"pending_reschedule": None},
                        user_id=state["user_db_id"],
                    )
```

- [ ] **Step 4: Rodar os testes**

```bash
uv run pytest tests/test_process_message.py::test_pending_reschedule_cleared_after_reschedule_tool_call -v
```

Expected: PASS.

- [ ] **Step 5: Rodar suite completa**

```bash
uv run pytest --tb=short -q
```

Expected: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat: limpar pending_reschedule após reschedule_appointment ou get_available_slots"
```

---

## Checklist pós-implementação (manual)

- [ ] Submeter o template `psique_reagendamento` no Meta Business Manager (WhatsApp Manager → Modelos de mensagem → Criar modelo → Utilitário → `pt_BR`)
- [ ] Aplicar a migration SQL no Supabase de produção
- [ ] Testar o script com um número real: `uv run python scripts/reschedule_notify.py --phone <phone> --appointment-id <uuid> --new-start <iso> --new-end <iso>`
- [ ] Responder ao WhatsApp com "sim" e verificar que o bot remarca automaticamente
- [ ] Responder com "prefiro outro horário" e verificar que o bot entra no fluxo de escolha
