# Explicação antecipada da 1ª consulta de menor com Dr. Júlio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a Eva explicar proativamente, durante o cadastro, que a primeira consulta de um menor de 18 anos com o Dr. Júlio é dividida em duas partes de 1 hora — assim que as 3 condições (menor + primeira consulta + Dr. Júlio) forem identificadas — em vez de depender do LLM lembrar disso só na hora de agendar.

**Architecture:** Novo campo booleano `minor_first_consult_explained` no `ConversationState`. `collect_info_node` (app/graph/nodes.py) checa a condição a cada turno, nos dois pontos de saída existentes (`_ask`/`_extract_and_ask`), e prefixa a explicação (uma bolha única) à próxima pergunta pendente do cadastro, marcando o flag. `patient_agent_node` passa a escolher entre duas variantes do prompt de agendamento para menor (`MINOR_RULE` completo como rede de segurança, ou `MINOR_RULE_SCHEDULING_ONLY` reduzido) conforme esse flag.

**Tech Stack:** Python, LangGraph (TypedDict state), pytest + unittest.mock (AsyncMock/patch), padrão de testes já existente em `tests/test_process_message.py`.

Spec de referência: `docs/superpowers/specs/2026-07-31-explicacao-antecipada-primeira-consulta-menor-design.md`

---

### Task 1: Novo campo de estado `minor_first_consult_explained`

**Files:**
- Modify: `app/graph/state.py:87` (final do `ConversationState`)

- [ ] **Step 1: Adicionar o campo ao `ConversationState`**

Ao final da classe (logo após `pending_booking_fee`), adicionar:

```python

    # True quando a Eva já explicou ao responsável, durante o cadastro, que a
    # primeira consulta de um paciente menor de idade com o Dr. Júlio é
    # dividida em duas partes de 1h. Evita repetir a explicação e sinaliza
    # para patient_agent_node que não precisa reexplicar antes de agendar.
    minor_first_consult_explained: bool | None
```

- [ ] **Step 2: Verificar que o módulo ainda importa sem erro**

Run: `uv run python -c "from app.graph.state import ConversationState; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/graph/state.py
git commit -m "feat(state): add minor_first_consult_explained field"
```

---

### Task 2: Novos textos de prompt em `app/graph/prompts.py`

**Files:**
- Modify: `app/graph/prompts.py:219` (logo após o fechamento de `MINOR_RULE`, antes de `MINOR_RETURNING_RULE`)

- [ ] **Step 1: Adicionar `MINOR_FIRST_CONSULT_INFO` e `MINOR_RULE_SCHEDULING_ONLY`**

Inserir entre a linha 219 (fim de `MINOR_RULE`) e a linha 221 (`MINOR_RETURNING_RULE = """\`):

```python
# Texto enviado pelo collect_info_node assim que as 3 condições (menor de
# idade + primeira consulta + Dr. Júlio) são identificadas — ver Task 3.
# Formatado como UMA bolha única de WhatsApp (sem quebras para bolhas
# separadas, ao contrário do texto completo em MINOR_RULE acima).
MINOR_FIRST_CONSULT_INFO = (
    "Como {patient_name} tem menos de 18 anos, a primeira consulta com o Dr. Júlio acontece em "
    "duas partes de 1 hora cada: a primeira é com os pais ou responsáveis, e a segunda é com "
    "{patient_name}. O mais comum é fazer as duas seguidas, totalizando 2h, mas também é possível "
    "marcar em dias diferentes — vamos combinar isso na hora de escolher o horário."
)

# Variante de MINOR_RULE usada quando a explicação acima já foi enviada
# durante o cadastro (minor_first_consult_explained=True) — só pergunta a
# preferência de logística, sem reexplicar a divisão em duas partes.
MINOR_RULE_SCHEDULING_ONLY = """\

REGRA IMPORTANTE — PACIENTE MENOR DE IDADE ({patient_age} anos) com Dr. Júlio (primeira consulta):
O responsável já foi informado de que a primeira consulta é dividida em duas partes de 1 hora \
(uma com os responsáveis, outra com {patient_name}). Antes de buscar horários, pergunte: \
"Prefere fazer as duas partes seguidas (2h) ou em dias/horários separados?"

SE o responsável preferir na sequência (2h seguidas):
- Use slot_duration_minutes=120 em get_available_slots e confirm_appointment.
- Deixe session_note vazio em confirm_appointment.

SE o responsável preferir em momentos separados:
- Agende a 1ª sessão (responsáveis): use slot_duration_minutes=60, \
session_note="1ª hora — responsáveis".
- Após confirmar a 1ª sessão, pergunte o dia e horário da 2ª sessão (paciente).
- Agende a 2ª sessão (paciente): use slot_duration_minutes=60, \
session_note="2ª hora — paciente".
"""
```

`MINOR_RULE` (linhas 196-219) **não muda** — continua servindo como rede de
segurança (texto completo: explica + pergunta) para quando
`minor_first_consult_explained` ainda não é `True` no momento do
agendamento.

- [ ] **Step 2: Verificar que o módulo importa e os textos formatam sem erro**

Run:
```bash
uv run python -c "
from app.graph.prompts import MINOR_FIRST_CONSULT_INFO, MINOR_RULE_SCHEDULING_ONLY, MINOR_RULE
print(MINOR_FIRST_CONSULT_INFO.format(patient_name='Bernardo'))
print('---')
print(MINOR_RULE_SCHEDULING_ONLY.format(patient_name='Bernardo', patient_age=10))
"
```
Expected: dois blocos de texto impressos sem `KeyError`/`IndexError` de
formatação.

- [ ] **Step 3: Commit**

```bash
git add app/graph/prompts.py
git commit -m "feat(prompts): add early minor-first-consult explanation and reduced scheduling rule"
```

---

### Task 3: Disparar a explicação em `collect_info_node`

**Files:**
- Modify: `app/graph/nodes.py:22` (import), `app/graph/nodes.py:412-454` (`_ask` e `_extract_and_ask`)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Escrever os testes que falham primeiro**

Adicionar ao final de `tests/test_process_message.py` (usar o helper
`_base_minor_state` já existente no arquivo, próximo à linha 423):

```python
async def test_collect_info_explains_minor_first_consult_when_doctor_confirmed_last():
    """Fluxo padrão: idade e 'primeira vez' já conhecidas, médico é a última das
    3 condições a ser confirmada (step 9 do cadastro) — a explicação antecipada
    deve sair prefixada à pergunta seguinte (e-mail) nesse mesmo turno."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _DOCTOR_Q = "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Bernardo", patient_cpf="111.222.333-00",
        is_patient=False, is_returning_patient=False,
        patient_age=10, birth_date="18/03/2016",
        guardian_name="Ana", guardian_cpf="111.222.333-44",
        preferred_doctor=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_DOCTOR_Q),
            HumanMessage(content="Dr Júlio"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("preferred_doctor") == "julio"
    assert result.get("minor_first_consult_explained") is True
    sent = mock_send.call_args[0][1]
    assert "duas partes de 1 hora" in sent
    assert "Bernardo" in sent
    assert "e-mail" in sent.lower()


async def test_collect_info_explains_minor_first_consult_when_doctor_known_from_first_message():
    """Caso 'Bernardo' real: o médico já foi mencionado na 1ª mensagem (auto-
    detectado antes dos steps), então is_returning_patient=False (step 4) é a
    última das 3 condições a ficar completa — a explicação deve sair
    prefixada à pergunta de CPF do paciente (step 5), não só na hora de agendar."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _PATIENT_Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        user_name="Bernardo Lima Beltrão Teixeira",
        patient_name="Bernardo Lima Beltrão Teixeira",
        patient_cpf=None,
        is_patient=False,
        is_returning_patient=None,
        patient_age=10, birth_date="18/03/2016",
        preferred_doctor="julio",  # já auto-detectado em turno anterior
        messages=[
            HumanMessage(content="Gostaria de marcar uma consulta com Dr Júlio"),
            AIMessage(content=_PATIENT_Q),
            HumanMessage(content="Primeira vez"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is False
    assert result.get("minor_first_consult_explained") is True
    sent = mock_send.call_args[0][1]
    assert "duas partes de 1 hora" in sent
    assert "cpf" in sent.lower()


async def test_collect_info_does_not_explain_minor_rule_for_adult():
    """Paciente adulto com Dr. Júlio nunca recebe a explicação de menor."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _DOCTOR_Q = "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?"
    state = _base_minor_state(
        user_name="Carlos", patient_name="Carlos", patient_cpf="111.222.333-00",
        is_patient=True, is_returning_patient=False,
        patient_age=35, birth_date="10/05/1989",
        preferred_doctor=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_DOCTOR_Q),
            HumanMessage(content="Dr Júlio"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("preferred_doctor") == "julio"
    assert not result.get("minor_first_consult_explained")
    sent = mock_send.call_args[0][1]
    assert "duas partes de 1 hora" not in sent


async def test_collect_info_does_not_explain_minor_rule_for_bruna():
    """Menor de idade escolhendo Dra. Bruna nunca recebe essa explicação
    (a divisão em 2 partes é específica do Dr. Júlio)."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _DOCTOR_Q = "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Joãozinho", patient_cpf="111.222.333-00",
        is_patient=False, is_returning_patient=False,
        patient_age=14, birth_date="18/03/2012",
        guardian_name="Ana", guardian_cpf="111.222.333-44",
        preferred_doctor=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_DOCTOR_Q),
            HumanMessage(content="Dra Bruna"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("preferred_doctor") == "bruna"
    assert not result.get("minor_first_consult_explained")
    sent = mock_send.call_args[0][1]
    assert "duas partes de 1 hora" not in sent


async def test_collect_info_does_not_explain_minor_rule_for_returning_patient():
    """Menor de idade RETORNANTE (is_returning_patient=True) com Dr. Júlio
    nunca recebe essa explicação — a divisão em 2 partes é só para a 1ª
    consulta."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _DOCTOR_Q = "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Joãozinho",
        is_patient=False, is_returning_patient=True,
        patient_age=14, birth_date="18/03/2012",
        guardian_name="Ana",
        preferred_doctor=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_DOCTOR_Q),
            HumanMessage(content="Dr Júlio"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("preferred_doctor") == "julio"
    assert not result.get("minor_first_consult_explained")
    sent = mock_send.call_args[0][1]
    assert "duas partes de 1 hora" not in sent


async def test_collect_info_does_not_repeat_explanation_once_sent():
    """Se minor_first_consult_explained já é True no estado de entrada, um
    turno seguinte do cadastro (aqui, respondendo o CPF do paciente) não deve
    reenviar a explicação — mesmo com as 3 condições continuando satisfeitas.

    Nota: não usar o passo de e-mail para este teste — quando e-mail é o
    último campo faltante, collect_info_node cai no fluxo que chama a LLM
    (não retorna via _ask/_extract_and_ask), então não é o cenário certo
    para testar o prefixo determinístico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _CPF_Q = "Qual o CPF do paciente?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Bernardo", patient_cpf=None,
        is_patient=False, is_returning_patient=False,
        patient_age=10, birth_date="18/03/2016",
        guardian_name=None, guardian_cpf=None,
        preferred_doctor="julio",
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_CPF_Q),
            HumanMessage(content="111.222.333-00"),
        ],
    )
    state["minor_first_consult_explained"] = True
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("patient_cpf") == "111.222.333-00"
    sent = mock_send.call_args[0][1]
    assert "duas partes de 1 hora" not in sent
    assert "responsável" in sent.lower()  # segue para a pergunta do nome do responsável
```

- [ ] **Step 2: Rodar os testes novos e confirmar que falham**

Run: `uv run pytest tests/test_process_message.py -k "minor_first_consult" -v`
Expected: FAIL em todos (campo/comportamento ainda não implementado) — os
testes que esperam ausência da explicação (`test_..._for_adult`,
`test_..._for_bruna`, `test_..._for_returning_patient`,
`test_..._does_not_repeat_explanation_once_sent`) já passam hoje (nada foi
implementado ainda, então nada é explicado) — apenas os dois primeiros
(`..._doctor_confirmed_last` e `..._first_message`) devem falhar.

- [ ] **Step 3: Adicionar o import dos novos textos de prompt**

Em `app/graph/nodes.py:22`, adicionar `MINOR_FIRST_CONSULT_INFO` e
`MINOR_RULE_SCHEDULING_ONLY` à lista de imports de `app.graph.prompts`
(mesma linha do import existente):

```python
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, MINOR_RETURNING_RULE, ADULT_RULE, GUARDIAN_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, CANCELLATION_RULES, CLINIC_ADDRESS, CLINIC_ADDRESS_TEXT, DOCTORS_INFO, sanitize_clinic_address, get_booking_fee_rule, MEDICAL_LIMITS_RULE, AGE_EXCEPTION_RULE, DOCTOR_CORRECTION_RULE, EMAIL_RULE, get_pricing_rules, ATTENDANT_INSTRUCTION_RULE, get_pricing_exception_rule, CORRECT_PIX_KEY, SOCIAL_NAME_RULE, MINOR_FIRST_CONSULT_INFO, MINOR_RULE_SCHEDULING_ONLY
```

- [ ] **Step 4: Adicionar os helpers e modificar `_ask`/`_extract_and_ask`**

Em `app/graph/nodes.py`, logo antes da definição de `async def _ask(reply: str) -> dict:` (linha 412), adicionar:

```python
    def _minor_first_explain_needed(extra: dict | None = None) -> bool:
        """True quando as 3 condições (menor de idade + primeira consulta +
        Dr. Júlio) estão completas neste turno e a explicação ainda não foi
        enviada. `extra` são os campos recém-extraídos neste turno (ainda não
        persistidos em `state`)."""
        merged = {**state, **_persistent_updates}
        if extra:
            merged.update(extra)
        age = merged.get("patient_age") or 99
        return (
            age < 18
            and merged.get("is_returning_patient") is False
            and merged.get("preferred_doctor") == "julio"
            and not merged.get("minor_first_consult_explained")
        )

    def _minor_first_explain_text(extra: dict | None = None) -> str:
        merged = {**state, **_persistent_updates}
        if extra:
            merged.update(extra)
        from app.utils import display_name as _dn
        full_name = (
            merged.get("social_name") or merged.get("patient_name")
            or merged.get("user_name") or "o paciente"
        )
        return MINOR_FIRST_CONSULT_INFO.format(patient_name=_dn(full_name))

```

Substituir o corpo de `_ask` (linhas 412-422 originais) por:

```python
    async def _ask(reply: str) -> dict:
        # Force stage back to collect_info: a caller upstream (e.g. an attendant
        # instruction in main.py) may have optimistically set stage="patient_agent"
        # before this turn even ran. Since we're asking another registration
        # question, registration is NOT complete — without this, _route_after_collect
        # would wrongly continue into patient_agent in the same turn, producing a
        # second, conflicting AI message and corrupting _last_ai()/_last_human()
        # bookkeeping for the next turn (see Talita/CPF incident 2026-07-03).
        extra_update: dict = {}
        if _minor_first_explain_needed():
            reply = _minor_first_explain_text() + "\n\n" + reply
            extra_update["minor_first_consult_explained"] = True
        await send_text(state["phone"], reply)
        await save_message(state["phone"], "assistant", reply)
        return {**_persistent_updates, **extra_update, "stage": "collect_info", "messages": [AIMessage(content=reply)]}
```

Substituir o corpo de `_extract_and_ask` (linhas 424-454 originais) por:

```python
    async def _extract_and_ask(extracted: dict, next_q: str) -> dict:
        """Persist extracted fields to Supabase and ask the next question in one turn."""
        _STATE_TO_DB = {
            "user_name": "name",
            "patient_name": "patient_name",
            "patient_cpf": "patient_cpf",
            "birth_date": "birth_date",
            "patient_age": "age",
            "guardian_name": "guardian_name",
            "guardian_cpf": "guardian_cpf",
            "guardian_relationship": "guardian_relationship",
            "is_patient": "is_patient",
            "is_returning_patient": "is_returning_patient",
            "patient_email": "email",
        }
        db_payload = {_STATE_TO_DB[k]: v for k, v in extracted.items() if k in _STATE_TO_DB}
        if "preferred_doctor" in extracted:
            db_payload["doctor_id"] = DOCTOR_IDS.get(extracted["preferred_doctor"])
        extra_update: dict = {}
        if next_q and _minor_first_explain_needed(extracted):
            next_q = _minor_first_explain_text(extracted) + "\n\n" + next_q
            extra_update["minor_first_consult_explained"] = True
        # See _ask() above for why stage is forced back to collect_info here too.
        result_update: dict = {**_persistent_updates, **extracted, **extra_update, "stage": "collect_info", "messages": [AIMessage(content=next_q)]}
        if db_payload:
            try:
                returned_id = await upsert_user(state["phone"], db_payload, user_id=state.get("user_db_id"))
                if returned_id and not state.get("user_db_id"):
                    result_update["user_db_id"] = returned_id
            except Exception:
                import logging as _log
                _log.getLogger(__name__).exception("Failed to persist partial collect_info data")
        await send_text(state["phone"], next_q)
        await save_message(state["phone"], "assistant", next_q)
        return result_update
```

- [ ] **Step 5: Rodar os testes novos e confirmar que passam**

Run: `uv run pytest tests/test_process_message.py -k "minor_first_consult" -v`
Expected: todos os 6 testes PASS.

- [ ] **Step 6: Rodar a suíte completa de `test_process_message.py` para checar regressão**

Run: `uv run pytest tests/test_process_message.py -v --tb=short`
Expected: todos os testes existentes continuam passando (nenhuma pergunta de
cadastro mudou de texto, só ganhou um prefixo condicional).

- [ ] **Step 7: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat(collect_info): explain minor-first-consult split as soon as the 3 conditions are known"
```

---

### Task 4: Usar a variante reduzida do prompt em `patient_agent_node`

**Files:**
- Modify: `app/graph/nodes.py:1535-1536`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Escrever os testes que falham primeiro**

Adicionar ao final de `tests/test_process_message.py` (usa os helpers
`_make_patient_agent_state`/`_run_patient_agent` já existentes no arquivo,
próximos à linha 1256):

```python
async def test_patient_agent_uses_reduced_minor_rule_when_already_explained():
    """Quando minor_first_consult_explained=True, o prompt de agendamento não
    deve reexplicar a divisão em duas partes — só pergunta a preferência."""
    state = _make_patient_agent_state(
        patient_age=10, birth_date="18/03/2016",
        is_returning_patient=False, preferred_doctor="julio",
        patient_name="Bernardo",
    )
    state["minor_first_consult_explained"] = True
    system_msg = await _run_patient_agent(state, last_assistant_time="2026-07-31T09:00:00+00:00")
    assert system_msg is not None
    assert "já foi informado" in system_msg.content
    assert "acontece em" not in system_msg.content  # trecho só do texto completo


async def test_patient_agent_uses_full_minor_rule_when_not_yet_explained():
    """Rede de segurança: se minor_first_consult_explained ainda não é True
    ao chegar em patient_agent_node, o prompt completo (explica + pergunta)
    continua sendo usado, como hoje."""
    state = _make_patient_agent_state(
        patient_age=10, birth_date="18/03/2016",
        is_returning_patient=False, preferred_doctor="julio",
        patient_name="Bernardo",
    )
    state["minor_first_consult_explained"] = None
    system_msg = await _run_patient_agent(state, last_assistant_time="2026-07-31T09:00:00+00:00")
    assert system_msg is not None
    assert "Antes de buscar horários, explique ao responsável" in system_msg.content
```

- [ ] **Step 2: Rodar os testes novos e confirmar que falham**

Run: `uv run pytest tests/test_process_message.py -k "reduced_minor_rule or full_minor_rule" -v`
Expected: `test_patient_agent_uses_reduced_minor_rule_when_already_explained` FAILS
(hoje sempre usa o texto completo); `test_patient_agent_uses_full_minor_rule_when_not_yet_explained`
já deve passar (comportamento atual inalterado).

- [ ] **Step 3: Implementar a seleção condicional**

Em `app/graph/nodes.py:1535-1536`, substituir:

```python
    if is_minor_first:
        duration_rule = MINOR_RULE.format(patient_name=first_name, patient_age=patient_age)
    elif is_minor:
```

por:

```python
    if is_minor_first:
        if state.get("minor_first_consult_explained"):
            duration_rule = MINOR_RULE_SCHEDULING_ONLY.format(patient_name=first_name, patient_age=patient_age)
        else:
            duration_rule = MINOR_RULE.format(patient_name=first_name, patient_age=patient_age)
    elif is_minor:
```

- [ ] **Step 4: Rodar os testes novos e confirmar que passam**

Run: `uv run pytest tests/test_process_message.py -k "reduced_minor_rule or full_minor_rule" -v`
Expected: os 2 testes PASS.

- [ ] **Step 5: Rodar a suíte completa para checar regressão**

Run: `uv run pytest tests/test_process_message.py -v --tb=short`
Expected: todos os testes passam, incluindo os já existentes que cobrem
`is_minor_first`/`duration_rule` (nenhum passava `minor_first_consult_explained=True`
antes, então o texto completo continua saindo para eles).

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py
git commit -m "feat(patient_agent): skip re-explaining minor-first-consult split when already explained"
```

---

### Task 5: Suíte completa e limpeza

**Files:** nenhum novo — apenas verificação.

- [ ] **Step 1: Rodar a suíte inteira do projeto**

Run: `uv run pytest --tb=short`
Expected: todos os testes passam (nenhuma regressão em outras áreas —
`test_tools.py`, `test_calendar.py`, etc. não foram tocados por este plano).

- [ ] **Step 2: Remover os scripts de investigação one-off do checkpoint anterior**

Os scripts `scripts/_check_5581987415206_convo.py`,
`scripts/_check_minor_history.py` e `scripts/_check_minor_history2.py` foram
criados durante a investigação (systematic-debugging) e não fazem parte da
funcionalidade. Repositório já tem convenção de manter scripts `_check_*.py`
como histórico de investigações — decidir com o usuário se ficam ou são
removidos antes do commit final (não commitar automaticamente sem
confirmação, já que remover arquivos não solicitados também não deve ser
feito silenciosamente).

- [ ] **Step 3: Revisão final do diff**

Run: `git log --oneline main..HEAD` e `git diff main...HEAD --stat`
Expected: 4 commits de feature (Tasks 1-4) tocando exatamente
`app/graph/state.py`, `app/graph/prompts.py`, `app/graph/nodes.py` e
`tests/test_process_message.py`.
