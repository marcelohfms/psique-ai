# Modality Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eva sempre pergunta ao paciente presencial ou online antes de confirmar um agendamento, exceto quando o slot é exclusivamente online ou o paciente tem `modality_restriction` no banco.

**Architecture:** Três camadas de mudança independentes: (1) adicionar `modality_restriction` ao `ConversationState` e carregá-lo do banco no `collect_info_node`; (2) atualizar o prompt para que o LLM respeite a restrição e sempre pergunte quando não há restrição; (3) fazer `confirm_appointment` e `reschedule_appointment` forçarem a restrição cadastral em `effective_modality` como salvaguarda.

**Tech Stack:** Python 3.14, LangGraph, Supabase, pytest + AsyncMock

---

## Arquivos modificados

| Arquivo | O que muda |
|---------|------------|
| `app/graph/state.py` | Adiciona campo `modality_restriction` ao `ConversationState` |
| `app/graph/nodes.py` | Lê `modality_restriction` do banco e popula o state (3 lugares) |
| `app/graph/prompts.py` | Substitui seção "MODALIDADE DE ATENDIMENTO" nos 2 prompts |
| `app/graph/tools.py` | `confirm_appointment` e `reschedule_appointment` respeitam `modality_restriction` do state |
| `tests/test_tools.py` | Novos testes para a restrição de modalidade |

---

### Task 1: Adicionar `modality_restriction` ao ConversationState

**Files:**
- Modify: `app/graph/state.py`

- [ ] **Step 1: Adicionar o campo ao TypedDict**

Em `app/graph/state.py`, acrescentar logo após `user_db_id`:

```python
# Modality restriction from DB: "online", "presencial", or None (no restriction)
modality_restriction: Literal["online", "presencial"] | None
```

O arquivo completo relevante fica:

```python
from typing import Annotated, Literal
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


ConversationStage = Literal["collect_info", "patient_agent", "human_handoff"]


class ConversationState(TypedDict):
    # LangGraph message history
    messages: Annotated[list, add_messages]

    # WhatsApp sender ID (e.g. "5583...@s.whatsapp.net")
    phone: str

    # Current stage in the conversation flow
    stage: ConversationStage

    # Who is contacting
    user_name: str | None

    # Patient data (may differ from contact when is_patient=False)
    patient_name: str | None
    patient_age: int | None        # determines 1h vs 2h slot

    # Clinic status
    is_patient: bool | None
    is_returning_patient: bool | None
    preferred_doctor: Literal["julio", "bruna"] | None

    # Relationship of contact to patient (only relevant when is_patient=False and patient is minor)
    guardian_relationship: str | None

    # Extended patient registration fields
    birth_date: str | None
    guardian_name: str | None
    guardian_cpf: str | None
    patient_email: str | None
    patient_cpf: str | None
    consultation_reason: str | None
    referral_professional: str | None
    medication_note: str | None

    # When True, Eva executes silently (attendant instruction via private note)
    silent_mode: bool | None

    # Multi-patient support
    pending_patients: list | None

    # DB id of the patient selected during disambiguation
    user_db_id: str | None

    # Modality restriction from DB: "online", "presencial", or None (no restriction)
    modality_restriction: Literal["online", "presencial"] | None
```

- [ ] **Step 2: Commit**

```bash
git add app/graph/state.py
git commit -m "feat: add modality_restriction field to ConversationState"
```

---

### Task 2: Carregar `modality_restriction` do banco no collect_info_node

**Files:**
- Modify: `app/graph/nodes.py`

Há três lugares em `collect_info_node` onde dados do usuário são carregados do banco para o state. Em todos, adicionar `"modality_restriction": u.get("modality_restriction")` (ou `selected.get(...)`).

- [ ] **Step 1: Localizar os três blocos de carregamento**

Os três blocos estão em `collect_info_node`:

1. **Single user auto-load** (~linha 103): dicionário `loaded = { "user_db_id": u["id"], ... }`
2. **Disambiguation selection** (~linha 150): dicionário de retorno com `"pending_patients": None, "user_db_id": selected["id"], ...`
3. **New user after collect_info completes** (~linha 507): `upsert_user` call (não aplica — esse bloco não carrega do banco, apenas salva)

- [ ] **Step 2: Adicionar `modality_restriction` no bloco 1 (single user)**

Localizar o dict `loaded = {` (linha ~103) e acrescentar a linha:

```python
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
}
```

- [ ] **Step 3: Adicionar `modality_restriction` no bloco 2 (disambiguation)**

Localizar o `return {` com `"pending_patients": None` (linha ~150) e acrescentar:

```python
return {
    "pending_patients": None,
    "user_db_id": selected["id"],
    "user_name": selected.get("name"),
    "patient_name": selected.get("patient_name") or selected.get("name"),
    "patient_age": selected.get("age"),
    "birth_date": selected.get("birth_date"),
    "is_patient": selected.get("is_patient"),
    "is_returning_patient": selected.get("is_returning_patient"),
    "preferred_doctor": doc_key,
    "patient_email": selected.get("email"),
    "guardian_name": selected.get("guardian_name"),
    "guardian_cpf": selected.get("guardian_cpf"),
    "guardian_relationship": selected.get("guardian_relationship"),
    "patient_cpf": selected.get("patient_cpf"),
    "modality_restriction": selected.get("modality_restriction"),
    "stage": "patient_agent",
    "messages": [],
}
```

- [ ] **Step 4: Rodar testes para garantir que nada quebrou**

```bash
uv run pytest tests/ --tb=short -q
```

Esperado: todos os testes passam (o campo novo é `None` por padrão via `.get()`).

- [ ] **Step 5: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat: load modality_restriction from DB into ConversationState"
```

---

### Task 3: Atualizar regra de modalidade nos prompts

**Files:**
- Modify: `app/graph/prompts.py`

A seção "MODALIDADE DE ATENDIMENTO" aparece duas vezes — uma em `RETURNING_PATIENT_SYSTEM` (~linha 454) e outra em `NEW_PATIENT_SYSTEM` (~linha 568). O novo texto deve substituir ambas.

- [ ] **Step 1: Identificar o texto atual a ser substituído**

O bloco atual (idêntico nos dois prompts) é:

```
MODALIDADE DE ATENDIMENTO (online ou presencial):
Após o paciente escolher o horário, siga esta lógica com base na indicação do slot:
- "[apenas online]": informe que este horário é exclusivamente online e passe modality="online" em confirm_appointment.
- "[online ou presencial — paciente escolhe livremente]": pergunte a preferência. INDEPENDENTE da resposta (online ou presencial), chame confirm_appointment com a modalidade escolhida. NÃO transfira para atendente.
- "[REQUER CONFIRMAÇÃO — online ou presencial sob consulta da atendente]": pergunte a preferência.
  - Se online: passe modality="online" em confirm_appointment normalmente.
  - Se presencial: use transfer_to_human (não chame confirm_appointment) para que a atendente confirme a disponibilidade.
  - EXCEÇÃO: se você estiver executando uma "[Instrução da atendente]" que já confirma a disponibilidade presencial, chame confirm_appointment com modality="presencial" diretamente — NÃO chame transfer_to_human novamente.
```

- [ ] **Step 2: Substituir por nova regra (use `replace_all=True` no Edit pois o texto é idêntico nos dois prompts)**

Novo texto:

```
MODALIDADE DE ATENDIMENTO (online ou presencial):
Após o paciente escolher o horário, aplique esta ordem de prioridade:

1. RESTRIÇÃO CADASTRAL — se {modality_restriction} estiver preenchido ("online" ou "presencial"):
   NÃO pergunte ao paciente. Informe: "Conforme seu cadastro, sua consulta será [online/presencial]."
   Passe modality="{modality_restriction}" em confirm_appointment.

2. SLOT "[apenas online]" (e sem restrição cadastral):
   NÃO pergunte. Informe que este horário é exclusivamente online e passe modality="online".

3. QUALQUER OUTRO CASO — slots "[online ou presencial — paciente escolhe livremente]" ou "[REQUER CONFIRMAÇÃO — online ou presencial sob consulta da atendente]":
   SEMPRE pergunte a preferência antes de confirmar. Então:
   - Se escolha livre: passe a preferência em confirm_appointment. NÃO transfira para atendente.
   - Se "[REQUER CONFIRMAÇÃO]" e escolheu presencial: use transfer_to_human para a atendente confirmar disponibilidade.
     EXCEÇÃO: se for "[Instrução da atendente]" que já confirma disponibilidade presencial, chame confirm_appointment com modality="presencial" diretamente.
   - Se "[REQUER CONFIRMAÇÃO]" e escolheu online: passe modality="online" em confirm_appointment normalmente.
```

- [ ] **Step 3: Adicionar `modality_restriction` como variável de template nos dois prompts**

Verificar se `modality_restriction` já está sendo passado como kwarg nas chamadas a `.format()` que montam os prompts. Buscar onde os prompts são formatados:

```bash
grep -n "RETURNING_PATIENT_SYSTEM\|NEW_PATIENT_SYSTEM\|\.format(" app/graph/prompts.py app/graph/nodes.py | head -30
```

- [ ] **Step 4: Passar `modality_restriction` no format() dos prompts**

Localizar as chamadas `.format(...)` que montam os prompts do sistema (em `prompts.py` ou `nodes.py`) e adicionar `modality_restriction=state.get("modality_restriction") or ""`.

- [ ] **Step 5: Rodar testes**

```bash
uv run pytest tests/ --tb=short -q
```

Esperado: todos passam.

- [ ] **Step 6: Commit**

```bash
git add app/graph/prompts.py app/graph/nodes.py
git commit -m "feat: add modality_restriction rule to Eva's scheduling prompts"
```

---

### Task 4: Forçar `modality_restriction` em confirm_appointment e reschedule_appointment

**Files:**
- Modify: `app/graph/tools.py`

Essa é a salvaguarda: mesmo que o LLM ignore a instrução do prompt e passe a modality errada, o código sobrescreve com a restrição cadastral.

- [ ] **Step 1: Escrever testes primeiro**

Em `tests/test_tools.py`, adicionar após os testes existentes de `presencial_sob_consulta`:

```python
async def test_confirm_appointment_respects_online_modality_restriction():
    """Se modality_restriction="online" no state, confirm_appointment ignora o modality arg."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rest-online") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="online"),
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — should be overridden
        )
    assert "evt-rest-online" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "online"


async def test_confirm_appointment_respects_presencial_modality_restriction():
    """Se modality_restriction="presencial" no state, confirm_appointment usa presencial."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rest-pres") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),
            config=CONFIG,
            modality="online",  # LLM passed online — should be overridden
        )
    assert "evt-rest-pres" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "presencial"


async def test_confirm_appointment_no_restriction_uses_slot_logic():
    """Sem restrição cadastral, a lógica de slot é aplicada normalmente."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-no-rest") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction=None),
            config=CONFIG,
            modality="presencial",
        )
    assert "evt-no-rest" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "presencial"
```

- [ ] **Step 2: Rodar os novos testes para confirmar que falham**

```bash
uv run pytest tests/test_tools.py::test_confirm_appointment_respects_online_modality_restriction tests/test_tools.py::test_confirm_appointment_respects_presencial_modality_restriction tests/test_tools.py::test_confirm_appointment_no_restriction_uses_slot_logic -v
```

Esperado: FAIL (a lógica de restrição ainda não existe).

- [ ] **Step 3: Implementar a restrição em `confirm_appointment`**

Em `app/graph/tools.py`, no bloco "Enforce modality constraints from schedule" de `confirm_appointment` (logo após `slot_constraint = get_modality_for_slot(doctor, start)`), adicionar a verificação de restrição ANTES da lógica de slot:

```python
    # Enforce modality constraints from schedule
    from app.google_calendar import get_modality_for_slot
    slot_constraint = get_modality_for_slot(doctor, start)

    # Patient-level restriction overrides everything (except it cannot enable presencial on online-only slots)
    restriction = state.get("modality_restriction")
    if restriction in ("online", "presencial"):
        # If slot is online-only, restriction "presencial" cannot override it
        effective_modality = "online" if slot_constraint == "online" else restriction
    elif slot_constraint == "online":
        effective_modality = "online"
    elif slot_constraint == "presencial_sob_consulta" and modality == "presencial":
        if state.get("silent_mode"):
            effective_modality = "presencial"
        else:
            patient_name_hint = patient_name_override.strip() or state.get("patient_name") or state.get("user_name", "paciente")
            doctor_hint = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
            slot_hint = start.strftime("%d/%m às %H:%M")
            return (
                "AÇÃO NECESSÁRIA: Este horário (quinta à tarde com o Dr. Júlio) pode ser presencial, "
                "mas a disponibilidade precisa ser confirmada pela atendente antes de agendar. "
                "Use transfer_to_human com o seguinte motivo exato: "
                f"'Confirmar disponibilidade presencial para {patient_name_hint} em {slot_hint} com {doctor_hint}. "
                f"Após confirmar, escreva nota privada: "
                f"Eva, pode agendar {patient_name_hint} para {slot_hint} com {doctor_hint}, modalidade presencial.'"
            )
    else:
        effective_modality = modality if modality in ("online", "presencial") else ""
```

- [ ] **Step 4: Implementar a restrição em `reschedule_appointment`**

Em `reschedule_appointment`, o bloco "Enforce modality constraints" (linha ~722) atualmente é:

```python
    effective_modality = "online" if slot_constraint == "online" else (modality if modality in ("online", "presencial") else "")
```

Substituir por:

```python
    restriction = state.get("modality_restriction")
    if restriction in ("online", "presencial"):
        effective_modality = "online" if slot_constraint == "online" else restriction
    else:
        effective_modality = "online" if slot_constraint == "online" else (modality if modality in ("online", "presencial") else "")
```

- [ ] **Step 5: Rodar todos os testes**

```bash
uv run pytest tests/ --tb=short -q
```

Esperado: todos os 113 + 3 novos passam.

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat: enforce modality_restriction in confirm/reschedule_appointment"
```

---

### Task 5: Verificar integração do template do prompt

**Files:**
- Modify: `app/graph/prompts.py` e/ou `app/graph/nodes.py` (conforme onde `.format()` é chamado)

- [ ] **Step 1: Localizar onde os prompts são montados**

```bash
grep -n "\.format(\|modality_restriction" app/graph/prompts.py app/graph/nodes.py
```

- [ ] **Step 2: Confirmar que `{modality_restriction}` é resolvido**

Se o prompt usa `.format(...)`, garantir que `modality_restriction` é passado. Se usa f-string ou outro mecanismo, adaptar o placeholder conforme o padrão existente.

Exemplo esperado (onde o prompt é formatado):

```python
system_prompt = RETURNING_PATIENT_SYSTEM.format(
    patient_name=patient_name,
    ...
    modality_restriction=state.get("modality_restriction") or "",
)
```

- [ ] **Step 3: Rodar testes finais**

```bash
uv run pytest tests/ --tb=short -q
```

Esperado: todos passam sem KeyError de template.

- [ ] **Step 4: Commit final**

```bash
git add app/graph/prompts.py app/graph/nodes.py
git commit -m "feat: wire modality_restriction into system prompt template"
```
