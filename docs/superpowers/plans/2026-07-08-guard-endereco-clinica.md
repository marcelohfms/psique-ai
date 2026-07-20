# Guard de Endereço da Clínica — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar um guardrail determinístico que intercepta e substitui qualquer resposta da Eva contendo um endereço da clínica divergente do correto (Recife-PE), antes de enviar ao WhatsApp.

**Architecture:** Extrai o texto puro do endereço correto para uma constante única (`CLINIC_ADDRESS_TEXT` em `app/graph/prompts.py`), compartilhada entre o system prompt (`CLINIC_ADDRESS`) e um novo guard em `app/graph/nodes.py` (`patient_agent_node`). O guard roda no mesmo ponto dos guards já existentes (`GUARD_PREMATURE_CONFIRM`, `GUARD_TRANSFER`): depois do `response = await _get_agent_llm().ainvoke(messages)`, antes do envio via `send_text`. Quando a resposta final (sem tool call) menciona "endereço" e contém um termo da lista de bloqueio (ex.: "rio de janeiro", "tijuca"), o guard descarta a resposta da LLM e a substitui por um `AIMessage` fixo com o endereço correto.

**Tech Stack:** Python, LangGraph, LangChain (`AIMessage`), pytest + `unittest.mock`.

Spec de referência: `docs/superpowers/specs/2026-07-08-guard-endereco-clinica-design.md`

---

### Task 1: Extrair `CLINIC_ADDRESS_TEXT` em `app/graph/prompts.py`

**Files:**
- Modify: `app/graph/prompts.py:204-213`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing test**

Adicione ao final de `tests/test_process_message.py` (nova seção):

```python
# ── Guard: wrong clinic address ──────────────────────────────────────────────

def test_clinic_address_text_is_embedded_in_clinic_address_prompt():
    """CLINIC_ADDRESS_TEXT é a fonte única do endereço — CLINIC_ADDRESS (prompt)
    deve conter exatamente esse texto, para nunca divergir do que o guard usa."""
    from app.graph.prompts import CLINIC_ADDRESS, CLINIC_ADDRESS_TEXT
    assert CLINIC_ADDRESS_TEXT in CLINIC_ADDRESS
    assert "Recife-PE" in CLINIC_ADDRESS_TEXT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py::test_clinic_address_text_is_embedded_in_clinic_address_prompt -v`
Expected: FAIL com `ImportError: cannot import name 'CLINIC_ADDRESS_TEXT'`

- [ ] **Step 3: Extract the constant**

Em `app/graph/prompts.py`, substitua (linhas 204-213):

```python
CLINIC_ADDRESS = """\

ENDEREÇO DA CLÍNICA — REGRA ABSOLUTA: use SOMENTE o endereço abaixo, palavra por palavra, \
sempre que informar onde fica a clínica. A clínica tem uma única unidade. NUNCA invente, \
altere ou mencione outra cidade, bairro, rua ou endereço.
RioMar Trade Center, Torre 1, Andar 25, Sala 2502
Av. República do Líbano, 251 — Recife-PE

PLANOS DE SAÚDE: A clínica NÃO aceita planos de saúde. O atendimento é exclusivamente particular.
"""
```

por:

```python
CLINIC_ADDRESS_TEXT = """\
RioMar Trade Center, Torre 1, Andar 25, Sala 2502
Av. República do Líbano, 251 — Recife-PE"""

CLINIC_ADDRESS = f"""\

ENDEREÇO DA CLÍNICA — REGRA ABSOLUTA: use SOMENTE o endereço abaixo, palavra por palavra, \
sempre que informar onde fica a clínica. A clínica tem uma única unidade. NUNCA invente, \
altere ou mencione outra cidade, bairro, rua ou endereço.
{CLINIC_ADDRESS_TEXT}

PLANOS DE SAÚDE: A clínica NÃO aceita planos de saúde. O atendimento é exclusivamente particular.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_process_message.py::test_clinic_address_text_is_embedded_in_clinic_address_prompt -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/prompts.py tests/test_process_message.py
git commit -m "refactor(prompts): extrai CLINIC_ADDRESS_TEXT como fonte única do endereço da clínica"
```

---

### Task 2: Adicionar o guard `GUARD_WRONG_ADDRESS` em `app/graph/nodes.py`

**Files:**
- Modify: `app/graph/nodes.py:20` (import)
- Modify: `app/graph/nodes.py:1648-1653` (novo bloco de guard)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Write the failing tests**

Adicione logo após o teste do Task 1 em `tests/test_process_message.py`:

```python
async def _run_patient_agent_with_response(state: dict, content: str, tool_calls=None):
    """Helper: roda patient_agent_node com uma resposta fixa da LLM e retorna
    (texto enviado ao paciente via send_text, dict de retorno do node)."""
    from app.graph.nodes import patient_agent_node

    ai_response = MagicMock()
    ai_response.tool_calls = tool_calls or []
    ai_response.content = content

    async def fake_ainvoke(messages):
        return ai_response

    sent = []

    async def fake_send_text(phone, text):
        sent.append(text)

    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", side_effect=fake_send_text), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.format_doctor_schedules", return_value="seg-sex"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        result = await patient_agent_node(state, CONFIG)

    sent_text = sent[0] if sent else None
    return sent_text, result


async def test_guard_blocks_hallucinated_rio_de_janeiro_address():
    """Regressão: conversa real 5581988054825 (08/07/2026) — a Eva respondeu com um
    endereço do Rio de Janeiro (Tijuca) mesmo com o endereço correto (Recife) no
    prompt. O guard deve substituir a resposta inteira pelo endereço correto."""
    from app.graph.prompts import CLINIC_ADDRESS_TEXT

    state = _make_patient_agent_state(
        messages=[HumanMessage(content="Me manda o endereço da clínica")],
    )
    sent_text, result = await _run_patient_agent_with_response(
        state,
        "O endereço da clínica é: Rua Conde de Bonfim, 344, sala 605 — Tijuca, Rio de Janeiro.",
    )

    assert sent_text is not None, "nenhuma mensagem enviada ao paciente"
    assert "Rio de Janeiro" not in sent_text
    assert "Tijuca" not in sent_text
    assert CLINIC_ADDRESS_TEXT in sent_text
    assert result["messages"][0].content == sent_text


async def test_guard_blocks_invented_two_units_address():
    """Regressão: na mesma conversa, a Eva chegou a inventar que a clínica tem
    'duas unidades' (Rio de Janeiro e Recife). Deve ser bloqueado também."""
    state = _make_patient_agent_state(
        messages=[HumanMessage(content="Então qual o endereço certo?")],
    )
    sent_text, _ = await _run_patient_agent_with_response(
        state,
        "A Clínica Psique possui duas unidades: uma no Rio de Janeiro (Tijuca) e outra em Recife.",
    )
    assert "duas unidades" not in sent_text.lower()


async def test_guard_does_not_touch_correct_address():
    """O endereço correto (Recife) mencionado pela LLM não deve ser alterado pelo guard."""
    state = _make_patient_agent_state(
        messages=[HumanMessage(content="Qual o endereço?")],
    )
    original = "O endereço da clínica é: RioMar Trade Center, Av. República do Líbano, 251 — Recife-PE."
    sent_text, _ = await _run_patient_agent_with_response(state, original)
    assert sent_text == original


async def test_guard_ignores_rio_de_janeiro_mention_unrelated_to_address():
    """Falso positivo: se a LLM menciona 'Rio de Janeiro' sem estar falando do
    endereço da clínica (ex.: comentando a cidade do paciente), o guard não deve
    disparar — falta a palavra-gatilho 'endereço'."""
    state = _make_patient_agent_state(
        messages=[HumanMessage(content="Sou do Rio de Janeiro, atendem online?")],
    )
    original = "Sim! Vi que você é do Rio de Janeiro — atendemos online normalmente, sem problema."
    sent_text, _ = await _run_patient_agent_with_response(state, original)
    assert sent_text == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_process_message.py -k guard_blocks_hallucinated_rio or guard_blocks_invented_two_units -v`
Expected: FAIL — `sent_text` ainda contém "Rio de Janeiro"/"Tijuca"/"duas unidades" (guard ainda não existe).

- [ ] **Step 3: Add the import**

Em `app/graph/nodes.py:20`, adicione `CLINIC_ADDRESS_TEXT` à lista de imports de `app.graph.prompts`:

```python
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, MINOR_RETURNING_RULE, ADULT_RULE, GUARDIAN_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, CANCELLATION_RULES, CLINIC_ADDRESS, CLINIC_ADDRESS_TEXT, DOCTORS_INFO, get_booking_fee_rule, MEDICAL_LIMITS_RULE, DOCTOR_CORRECTION_RULE, EMAIL_RULE, get_pricing_rules, ATTENDANT_INSTRUCTION_RULE, get_pricing_exception_rule, CORRECT_PIX_KEY
```

- [ ] **Step 4: Add the guard block**

Em `app/graph/nodes.py`, localize o final do guard `GUARD_PREMATURE_CONFIRM` (linhas 1648-1653):

```python
            return {
                "messages": [_AIMsg2(content=_summary)],
                "pending_appointment": _pending_from_args,
            }

    # ── Guard: catch promised-but-not-called transfer_to_human ──────────────
```

Substitua por (insere o novo guard entre os dois existentes):

```python
            return {
                "messages": [_AIMsg2(content=_summary)],
                "pending_appointment": _pending_from_args,
            }

    # ── Guard: block hallucinated clinic address ────────────────────────────
    # A Eva já alucinou um endereço do Rio de Janeiro numa conversa real, mesmo
    # com o endereço correto (Recife) presente no system prompt. Se a resposta
    # final menciona "endereço" e cita um termo de uma cidade/local incorretos,
    # descarta a resposta da LLM e substitui pelo endereço correto. Roda antes
    # do GUARD_TRANSFER abaixo, para que ele veja o conteúdo já corrigido.
    _WRONG_ADDRESS_TERMS = (
        "rio de janeiro", "tijuca", "conde de bonfim",
        "duas unidades", "duas localizações", "duas localizacoes",
    )
    if not response.tool_calls and response.content:
        _addr_lower = response.content.lower()
        _mentions_address = "endereço" in _addr_lower or "endereco" in _addr_lower
        if _mentions_address and any(term in _addr_lower for term in _WRONG_ADDRESS_TERMS):
            _logger.warning(
                "GUARD_WRONG_ADDRESS: blocked wrong address phone=%s original=%s",
                state["phone"], response.content,
            )
            response = AIMessage(content=f"O endereço da clínica é:\n{CLINIC_ADDRESS_TEXT}")

    # ── Guard: catch promised-but-not-called transfer_to_human ──────────────
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_message.py -k "guard_blocks_hallucinated_rio or guard_blocks_invented_two_units or guard_does_not_touch_correct_address or guard_ignores_rio_de_janeiro" -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "fix(guard): bloqueia endereço da clínica alucinado antes do envio ao paciente"
```

---

### Task 3: Rodar a suíte completa e confirmar não-regressão

**Files:** nenhum arquivo novo — apenas verificação.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --tb=short`
Expected: todos os testes passam (nenhuma regressão nos guards existentes `GUARD_PREMATURE_CONFIRM`/`GUARD_TRANSFER` nem no restante do `patient_agent_node`).

- [ ] **Step 2: If everything passes, no further action needed**

Se algum teste pré-existente quebrar, investigar antes de prosseguir — não há necessidade de commit adicional nesta etapa se tudo passar de primeira.

---

## Fora de escopo (YAGNI) — não implementar neste plano

- Alerta para a clínica (nota privada/e-mail) quando o guard disparar.
- Reescrita parcial do trecho do endereço.
- Checagem via LLM separada.
- Guardrails para outros tipos de alucinação (preços, políticas).
