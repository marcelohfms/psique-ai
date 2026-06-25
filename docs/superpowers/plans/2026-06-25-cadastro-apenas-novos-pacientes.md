# Cadastro apenas para novos pacientes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Para pacientes que já são da clínica (`is_returning_patient=true`), pular a coleta de dados cadastrais que já existem (CPF do paciente e, para menores, CPF do responsável), perguntando "já é paciente?" cedo no fluxo.

**Architecture:** O fluxo de coleta é dirigido por uma máquina de estados hard-coded dentro de `collect_info_node` (`app/graph/nodes.py`), não pelo prompt (o prompt FASE 2 é fallback do LLM). A mudança central: (1) extrair a ordem canônica das perguntas para uma única função `_next_question`, com as regras de ramificação (novo vs. retornante, menor vs. adulto); (2) reordenar os blocos de detecção de resposta para casar com a nova ordem e adicionar os "gates" de paciente novo; (3) afrouxar `is_registration_complete` para exigir `guardian_cpf` só de menores novos.

**Tech Stack:** Python, LangGraph, pytest. Testes mockam `send_text`/`save_message`/`get_users_by_phone`/`upsert_user`.

---

## Nova ordem canônica das perguntas

```
1. user_name
2. is_patient            (própria pessoa ou outra)
3. patient_name          (se is_patient=False)
4. birth_date            (calcula idade)
5. is_returning_patient  ← MOVIDO para cá ("já é paciente?")
6. patient_cpf           ← SÓ se novo (is_returning_patient=False)
7. guardian_name         (se menor) — todos os menores
8. guardian_cpf          (se menor) ← SÓ se novo
9. preferred_doctor
10. patient_email
```

`guardian_relationship` continua sendo inferido pelo LLM (não é etapa hard-coded).
`consultation_reason` / `referral_professional` continuam exclusivos de novos pacientes (já hoje, fora da máquina de estados).

---

## File Structure

- Modify: `app/database.py` — `is_registration_complete` (Parte 2)
- Modify: `app/graph/nodes.py` — `collect_info_node`: novo helper `_next_question`, reordenação dos blocos, gates (Parte 1)
- Modify: `app/graph/prompts.py` — texto FASE 2 para consistência com o novo fluxo
- Modify: `tests/test_database_shim.py` — casos de `is_registration_complete`
- Modify: `tests/test_process_message.py` — casos de `collect_info_node` para a nova ordem

---

## Task 1: `is_registration_complete` exige `guardian_cpf` só de menores novos

**Files:**
- Modify: `app/database.py:235-282`
- Test: `tests/test_database_shim.py`

- [ ] **Step 1: Write the failing tests**

Adicione ao fim de `tests/test_database_shim.py`:

```python
def _complete_minor(**overrides) -> dict:
    """Base de um cadastro de MENOR completo (estilo dict legado de users)."""
    u = {
        "name": "Maria Silva",
        "email": "maria@x.com",
        "birth_date": "2016-03-10",
        "doctor_id": "dr-julio",
        "is_patient": False,
        "patient_name": "João Silva",
        "age": 10,
        "guardian_name": "Maria Silva",
        "guardian_relationship": "mãe",
        "guardian_cpf": "555",
        "is_returning_patient": True,
    }
    u.update(overrides)
    return u


def test_minor_returning_without_guardian_cpf_is_complete():
    # Paciente menor que JÁ é da clínica não precisa de guardian_cpf.
    u = _complete_minor(is_returning_patient=True, guardian_cpf=None)
    assert is_registration_complete(u) is True


def test_minor_new_without_guardian_cpf_is_incomplete():
    # Paciente menor NOVO ainda exige guardian_cpf (regressão preservada).
    u = _complete_minor(is_returning_patient=False, guardian_cpf=None)
    assert is_registration_complete(u) is False


def test_minor_returning_still_requires_guardian_name_and_relationship():
    assert is_registration_complete(_complete_minor(guardian_name=None)) is False
    assert is_registration_complete(_complete_minor(guardian_relationship=None)) is False


def test_adult_returning_without_patient_cpf_is_complete():
    u = {
        "name": "Ana Souza", "email": "ana@x.com", "birth_date": "1990-08-22",
        "doctor_id": "dra-bruna", "is_patient": True, "age": 35,
        "is_returning_patient": True,
    }
    assert is_registration_complete(u) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database_shim.py -k "guardian_cpf or returning_still_requires or adult_returning" -v`
Expected: `test_minor_returning_without_guardian_cpf_is_complete` FAILS (hoje exige guardian_cpf de todo menor). Os demais podem passar.

- [ ] **Step 3: Implement**

Em `app/database.py`, substitua o bloco "Minor-specific requirements" (linhas ~275-280):

```python
    # Minor-specific requirements
    age = user.get("age")
    if age is not None and age < 18:
        # guardian_name e guardian_relationship são obrigatórios para todo menor.
        # guardian_cpf é obrigatório apenas para pacientes NOVOS — pacientes que
        # já são da clínica já têm o CPF do responsável no cadastro.
        required_minor = ["guardian_name", "guardian_relationship"]
        if user.get("is_returning_patient") is False:
            required_minor.append("guardian_cpf")
        for field in required_minor:
            if not user.get(field):
                return False
```

Atualize também a docstring (linhas ~247-250) para refletir que `guardian_cpf` só é exigido de menores novos.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_database_shim.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_database_shim.py
git commit -m "feat: guardian_cpf obrigatório apenas para menores novos em is_registration_complete

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `_next_question` helper + reordenação da máquina de estados

**Files:**
- Modify: `app/graph/nodes.py` — `collect_info_node` (helper após linha ~365; greeting ~367-399; blocos ~401-590)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing tests**

Adicione a `tests/test_process_message.py` (usam o helper `_base_minor_state` já existente):

```python
async def test_collect_info_birth_date_leads_to_is_returning_question():
    """Após a data de nascimento, a próxima pergunta é 'já é paciente?'."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Ana", patient_name="Ana", patient_cpf=None,
        is_patient=True, birth_date=None, patient_age=None,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual a data de nascimento do paciente? (formato dd/mm/aaaa)"),
            HumanMessage(content="22/08/1990"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("birth_date") == "22/08/1990"
    sent = mock_send.call_args[0][1].lower()
    assert "primeira consulta" in sent or "já está em acompanhamento" in sent


async def test_collect_info_returning_adult_skips_cpf_goes_to_doctor():
    """Adulto que JÁ é paciente: após 'já é paciente?'=sim, pula CPF e vai ao médico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Ana", patient_cpf=None,
        is_patient=True, patient_age=35, birth_date="22/08/1990",
        is_returning_patient=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_Q),
            HumanMessage(content="já sou paciente"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is True
    sent = mock_send.call_args[0][1].lower()
    assert "cpf" not in sent
    assert "júlio" in sent or "bruna" in sent


async def test_collect_info_new_adult_asks_cpf_after_is_returning():
    """Adulto NOVO: após 'já é paciente?'=não, a próxima pergunta é o CPF."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Ana", patient_cpf=None,
        is_patient=True, patient_age=35, birth_date="22/08/1990",
        is_returning_patient=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_Q),
            HumanMessage(content="é a primeira vez"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is False
    sent = mock_send.call_args[0][1].lower()
    assert "cpf" in sent


async def test_collect_info_returning_minor_guardian_name_skips_guardian_cpf():
    """Menor que JÁ é paciente: após nome do responsável, pula CPF do responsável e vai ao médico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Maria Souza", patient_name="Pedro Lima", patient_cpf=None,
        is_patient=False, patient_age=10, birth_date="15/03/2015",
        is_returning_patient=True, guardian_name=None, guardian_cpf=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content="Qual é o nome completo do responsável pelo paciente?"),
            HumanMessage(content="Maria Souza"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("guardian_name") == "Maria Souza"
    sent = mock_send.call_args[0][1].lower()
    assert "cpf" not in sent
    assert "júlio" in sent or "bruna" in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k "is_returning_question or returning_adult_skips or new_adult_asks_cpf or returning_minor_guardian" -v`
Expected: FAIL (fluxo atual pede CPF antes de nascimento e antes de is_returning).

- [ ] **Step 3: Add the `_next_question` helper**

Em `app/graph/nodes.py`, logo após a linha `_MED_Q = ...` (~365), adicione:

```python
    # ── Ordem canônica das perguntas do cadastro ─────────────────────────────
    # Retorna a próxima pergunta a fazer, ou None quando todos os campos
    # obrigatórios da ramificação atual já estão preenchidos.
    # Pacientes que JÁ são da clínica (is_returning_patient=True) pulam
    # patient_cpf e, para menores, guardian_cpf — esses dados já existem no
    # cadastro da clínica.
    _NQ_KEYS = (
        "user_name", "is_patient", "patient_name", "birth_date",
        "is_returning_patient", "patient_cpf", "patient_age",
        "guardian_name", "guardian_cpf", "preferred_doctor", "patient_email",
    )

    def _next_question(s: dict) -> str | None:
        age = s.get("patient_age") or 99
        minor = age < 18
        returning = s.get("is_returning_patient")
        is_new = returning is False
        if not s.get("user_name"):
            return _NAME_Q
        if s.get("is_patient") is None:
            return _IS_PATIENT_Q
        if s.get("is_patient") is False and not s.get("patient_name"):
            return _PATIENT_NAME_Q
        if not s.get("birth_date"):
            return _BIRTH_Q
        if returning is None:
            return _PATIENT_Q
        if is_new and not s.get("patient_cpf"):
            return _CPF_Q
        if minor and not s.get("guardian_name"):
            return _GUARDIAN_NAME_Q
        if minor and is_new and not s.get("guardian_cpf"):
            return _GUARDIAN_CPF_Q
        if not s.get("preferred_doctor"):
            return _DOCTOR_Q
        if not s.get("patient_email"):
            return _EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO
        return None

    def _nq(**extra) -> str | None:
        merged = {k: state.get(k) for k in _NQ_KEYS}
        merged.update(extra)
        return _next_question(merged)
```

- [ ] **Step 4: Replace the greeting chain (Step 1)**

Substitua o bloco da linha ~367-399 (`# Step 1: greeting ...` até o `return await _ask(greeting)`) por:

```python
    # Step 1: greeting + first MISSING question (skip fields already in state)
    if not _has_greeted and _has_request:
        first_q = _nq()
        if first_q:
            greeting = (
                "Olá! 😊 Sou a Eva, assistente virtual da Clínica Psique.\n\n"
                "Claro, posso te ajudar com isso! Mas primeiro precisarei colher algumas informações.\n\n"
                + first_q
            )
            return await _ask(greeting)
```

- [ ] **Step 5: Reorder and re-wire the answer-detection blocks (Steps 2-7)**

Substitua todo o intervalo da linha ~407 (`# Step 2: contact name`) até ~518 (fim do bloco `# Step 4c: guardian CPF`, antes de `# Step 5: is_returning_patient`) — ou seja, os blocos Steps 2 a 4c — pela sequência abaixo, **nesta ordem**. Em seguida o bloco `# Step 5: is_returning_patient` (atual ~520-537) deve ser **movido para ANTES** dos blocos de CPF/guardian (já está incluído na ordem abaixo). Resultado final dos blocos:

```python
        # Step 2: contact name — saved to contacts.name only
        if not state.get("user_name"):
            if last_ai and _NAME_Q in last_ai and last_human:
                return await _extract_and_ask({"user_name": last_human}, _nq(user_name=last_human))
            return await _ask(_NAME_Q)

        # Step 2b: is the contact the patient or scheduling for someone else?
        if state.get("is_patient") is None:
            _asked_is_patient = (
                _IS_PATIENT_Q in last_ai
                or "para você ou" in last_ai.lower()
                or "para outra pessoa" in last_ai.lower()
            )
            if _asked_is_patient and last_human:
                h = last_human.lower()
                _not_patient_kws = [
                    "não", "nao", "mãe", "mae", "pai", "filho", "filha",
                    "em nome", "para meu", "para minha", "esposo", "esposa",
                    "marido", "irmão", "irmao", "irma", "outra", "outra pessoa",
                ]
                is_pat = not any(kw in h for kw in _not_patient_kws)
                if is_pat:
                    _uname = state.get("user_name", "")
                    return await _extract_and_ask(
                        {"is_patient": True, "patient_name": _uname},
                        _nq(is_patient=True, patient_name=_uname),
                    )
                else:
                    return await _extract_and_ask(
                        {"is_patient": False}, _nq(is_patient=False)
                    )
            return await _ask(_IS_PATIENT_Q)

        # Step 2c: patient name (only when contact is scheduling for someone else)
        if state.get("is_patient") is False and not state.get("patient_name"):
            if last_ai and _PATIENT_NAME_Q in last_ai and last_human:
                return await _extract_and_ask(
                    {"patient_name": last_human}, _nq(patient_name=last_human)
                )
            return await _ask(_PATIENT_NAME_Q)

        # Step 3: birth date — calcula a idade
        if not state.get("birth_date"):
            asked_birth = "nascimento" in last_ai.lower()
            if asked_birth and last_human:
                parsed = _parse_birth_date(last_human)
                if parsed:
                    bd = datetime.strptime(parsed, "%d/%m/%Y")
                    today = datetime.now()
                    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                    return await _extract_and_ask(
                        {"birth_date": parsed, "patient_age": age},
                        _nq(birth_date=parsed, patient_age=age),
                    )
                else:
                    return await _ask("Não consegui identificar a data. Pode informar no formato dd/mm/aaaa? Ex: 15/01/1990.")
            return await _ask(_BIRTH_Q)

        # Step 4: is_returning_patient
        # "já é paciente da clínica?" → True = retornante, False = novo.
        # is_patient (contato é o paciente vs. agenda para outro) é separado e
        # NÃO deve ser setado aqui.
        if state.get("is_returning_patient") is None:
            if last_ai and _PATIENT_Q in last_ai and last_human:
                h = last_human.lower()
                if any(kw in h for kw in ["sim", "já", "ja", "sou", "é", "e paciente", "paciente"]):
                    is_returning_patient = True
                elif any(kw in h for kw in ["não", "nao", "nunca", "primeira", "novo", "nova"]):
                    is_returning_patient = False
                else:
                    is_returning_patient = None
                if is_returning_patient is not None:
                    return await _extract_and_ask(
                        {"is_returning_patient": is_returning_patient},
                        _nq(is_returning_patient=is_returning_patient),
                    )
            return await _ask(_PATIENT_Q)

        # Step 5: CPF do paciente — apenas para pacientes NOVOS; opcional p/ estrangeiros
        if state.get("is_returning_patient") is False and not state.get("patient_cpf"):
            if last_ai and _CPF_Q in last_ai and last_human:
                import re as _re
                _foreign_kws = [
                    "não tenho", "nao tenho", "estrangeiro", "estrangeira",
                    "não possuo", "nao possuo", "passport", "passaporte",
                    "sem cpf", "não tem cpf", "nao tem cpf",
                ]
                if any(kw in last_human.lower() for kw in _foreign_kws):
                    return await _extract_and_ask({"patient_cpf": "N/A"}, _nq(patient_cpf="N/A"))
                elif _re.search(r'\d', last_human):
                    return await _extract_and_ask({"patient_cpf": last_human}, _nq(patient_cpf=last_human))
                else:
                    return await _ask(
                        "CPF inválido. Por favor, informe o CPF do paciente com os números (ex: 123.456.789-10).\n"
                        "Caso o paciente não tenha CPF (estrangeiro), responda \"não tenho CPF\"."
                    )
            return await _ask(_CPF_Q)

        # Step 6: guardian name (todos os menores)
        if (state.get("patient_age") or 99) < 18 and not state.get("guardian_name"):
            _last_ai_asked_guardian_name = last_ai and (
                _GUARDIAN_NAME_Q in last_ai
                or "responsável" in last_ai.lower()
                or "nome completo do" in last_ai.lower()
            )
            if _last_ai_asked_guardian_name and last_human:
                return await _extract_and_ask(
                    {"guardian_name": last_human, "user_name": last_human},
                    _nq(guardian_name=last_human),
                )
            return await _ask(_GUARDIAN_NAME_Q)

        # Step 7: guardian CPF (menores) — apenas para pacientes NOVOS; opcional p/ estrangeiros
        if (state.get("patient_age") or 99) < 18 \
                and state.get("is_returning_patient") is False \
                and not state.get("guardian_cpf"):
            _last_ai_asked_guardian_cpf = last_ai and (
                _GUARDIAN_CPF_Q in last_ai
                or ("cpf" in last_ai.lower() and "responsável" in last_ai.lower())
                or ("cpf" in last_ai.lower() and (state.get("guardian_name") or "").split()[0].lower() in last_ai.lower())
            )
            if _last_ai_asked_guardian_cpf and last_human:
                _foreign_kws = [
                    "não tenho", "nao tenho", "estrangeiro", "estrangeira",
                    "não possuo", "nao possuo", "passport", "passaporte",
                    "sem cpf", "não tem cpf", "nao tem cpf",
                ]
                if any(kw in last_human.lower() for kw in _foreign_kws):
                    return await _extract_and_ask({"guardian_cpf": "N/A"}, _nq(guardian_cpf="N/A"))
                return await _extract_and_ask({"guardian_cpf": last_human}, _nq(guardian_cpf=last_human))
            return await _ask(_GUARDIAN_CPF_Q)
```

Em seguida, o bloco antigo `# Step 5: is_returning_patient` (original ~520-537) deve ser REMOVIDO (já foi reposicionado como "Step 4" acima). Os blocos `# Step 6: preferred doctor`, `# Step 7: email` e `# Step 8: medication` permanecem inalterados (apenas renumere os comentários se desejar: doctor, email, medication).

- [ ] **Step 6: Run the new tests**

Run: `uv run pytest tests/test_process_message.py -k "is_returning_question or returning_adult_skips or new_adult_asks_cpf or returning_minor_guardian" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat: perguntar 'já é paciente?' cedo e pular dados cadastrais de retornantes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Reparar os testes existentes de collect_info para a nova ordem

**Files:**
- Modify: `tests/test_process_message.py`

Três testes assumem a ordem antiga (CPF antes de nascimento; guardian antes de is_returning) e vão quebrar.

- [ ] **Step 1: Rodar a suíte para ver as quebras**

Run: `uv run pytest tests/test_process_message.py -v`
Expected: falham `test_collect_info_asks_guardian_name_after_minor_birth_date`, `test_collect_info_asks_guardian_cpf_after_guardian_name`, `test_collect_info_proceeds_to_is_returning_after_guardian_cpf`.

- [ ] **Step 2: Atualizar `test_collect_info_asks_guardian_name_after_minor_birth_date`**

Na nova ordem, após a data de nascimento de um menor a próxima pergunta é "já é paciente?", não o responsável. Reescreva a asserção final:

```python
    assert result.get("birth_date") == "15/03/2015"
    assert result.get("patient_age") == expected_age
    assert expected_age < 18, "Test pre-condition: patient must be a minor"
    sent = mock_send.call_args[0][1].lower()
    assert "primeira consulta" in sent or "já está em acompanhamento" in sent
```

- [ ] **Step 3: Atualizar `test_collect_info_asks_guardian_cpf_after_guardian_name`**

O CPF do responsável só é pedido para menores NOVOS. Adicione `is_returning_patient=False` ao estado:

```python
    state = _base_minor_state(
        user_name="Maria Souza",
        patient_name="Pedro Lima",
        patient_cpf=None,
        is_patient=False,
        is_returning_patient=False,   # menor NOVO → pede CPF do responsável
        patient_age=10,
        birth_date="15/03/2015",
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual é o nome completo do responsável pelo paciente?"),
            HumanMessage(content="Maria Souza"),
        ],
    )
```

(O restante do teste — assert `guardian_name` e `"cpf" in sent` — continua válido.)

- [ ] **Step 4: Atualizar `test_collect_info_proceeds_to_is_returning_after_guardian_cpf`**

Na nova ordem, is_returning é perguntado ANTES do responsável, então após o CPF do responsável (menor novo) a próxima pergunta é o médico. Renomeie e reescreva:

```python
async def test_collect_info_new_minor_after_guardian_cpf_goes_to_doctor():
    """Menor NOVO: após CPF do responsável, a próxima pergunta é o médico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Maria Souza",
        patient_name="Pedro Lima",
        patient_cpf="111.222.333-44",
        is_patient=False,
        is_returning_patient=False,
        patient_age=10,
        birth_date="15/03/2015",
        guardian_name="Maria Souza",
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual é o CPF do responsável?"),
            HumanMessage(content="123.456.789-00"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("guardian_cpf") == "123.456.789-00"
    sent = mock_send.call_args[0][1].lower()
    assert "júlio" in sent or "bruna" in sent
```

- [ ] **Step 5: Rodar a suíte de process_message**

Run: `uv run pytest tests/test_process_message.py -v`
Expected: PASS (todos).

- [ ] **Step 6: Commit**

```bash
git add tests/test_process_message.py
git commit -m "test: ajustar testes de collect_info para a nova ordem do cadastro

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Atualizar o prompt FASE 2 para consistência

**Files:**
- Modify: `app/graph/prompts.py:83-156`

O prompt é fallback do LLM, mas deve refletir o mesmo comportamento para os casos em que o LLM assume a coleta.

- [ ] **Step 1: Reordenar a lista de informações**

Em `app/graph/prompts.py`, na seção "Informações necessárias (em ordem)" (~87-114), reordene para: user_name, is_patient, patient_name, birth_date, **is_returning_patient**, patient_cpf, guardian_relationship, guardian_name, guardian_cpf, preferred_doctor, patient_email, consultation_reason, referral_professional. Marque explicitamente:

- `patient_cpf` — "pergunte SOMENTE se is_returning_patient=false (paciente novo); para paciente que já é da clínica, pule."
- `guardian_cpf` — "pergunte SOMENTE se paciente < 18 anos E is_returning_patient=false; para menor que já é paciente, pule."
- `guardian_name` e `guardian_relationship` — exigidos para todo menor (novo ou retornante).

- [ ] **Step 2: Ajustar as Regras**

Atualize as duas regras correspondentes (~127-133):

- Substitua "patient_cpf: pergunte sempre (adultos e menores)..." por uma regra que pergunta o CPF do paciente apenas quando `is_returning_patient=false`.
- Na regra "CRÍTICO — MENORES DE IDADE" (~130-133), troque a exigência de `guardian_cpf` para aplicar-se apenas a menores com `is_returning_patient=false`. Para menor que já é paciente, exija apenas `guardian_name` e `guardian_relationship`.

- [ ] **Step 3: Rodar a suíte completa**

Run: `uv run pytest --tb=short`
Expected: PASS (o prompt não tem teste dedicado; confirme que nada regrediu).

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "docs: alinhar prompt FASE 2 com cadastro reduzido para retornantes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Verificação final

- [ ] **Step 1: Suíte completa**

Run: `uv run pytest --tb=short`
Expected: todos os testes PASS.

- [ ] **Step 2: Sanidade manual do fluxo (opcional)**

Confirme mentalmente os caminhos:
- Adulto retornante: nome → é p/ você? → nascimento → "já é paciente?" sim → médico → e-mail. (sem CPF)
- Adulto novo: ... → "já é paciente?" não → CPF → médico → e-mail.
- Menor retornante: ... → nascimento → "já é paciente?" sim → nome do responsável → médico → e-mail. (sem CPF do paciente nem do responsável)
- Menor novo: ... → "já é paciente?" não → CPF → nome do responsável → CPF do responsável → médico → e-mail.

- [ ] **Step 3: Merge/finalização**

Use a skill superpowers:finishing-a-development-branch para decidir merge/PR.

---

## Self-Review (preenchido pelo autor do plano)

- **Cobertura do spec:** Parte 1 (ordem + skip) → Tasks 2, 3, 4. Parte 2 (`is_registration_complete`) → Task 1. Testes do spec → Tasks 1-3. ✅
- **Placeholders:** nenhum — todo passo tem código/comando concreto. ✅
- **Consistência de tipos/nomes:** `_next_question`/`_nq`, `is_returning_patient`, `_CPF_Q`/`_GUARDIAN_CPF_Q`/`_PATIENT_Q`/`_DOCTOR_Q` usados consistentemente; constantes já existem em `nodes.py`. ✅
