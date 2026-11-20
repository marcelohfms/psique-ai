# Guard: registrar receita pedida junto com mudança de dose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Registrar a solicitação de receita (documents + evento + planilha + e-mail à médica) quando o paciente pede emissão junto de uma dúvida de dose e a Eva responde só com texto, sem mudar a mensagem que o paciente vê.

**Architecture:** Fatorar o miolo da tool `request_document` numa função async reutilizável, e adicionar no `patient_agent_node` um guard que, detectando pedido de emissão na mensagem do paciente sem nenhuma ação de documento/handoff no turno, chama essa função por baixo e devolve a resposta da Eva intacta.

**Tech Stack:** Python, LangGraph, pytest (mocks para Supabase/Sheets/e-mail).

**Spec:** docs/superpowers/specs/2026-11-16-guard-receita-com-dose-design.md

## Global Constraints

- Nenhuma mudança no prompt (`app/graph/prompts.py`). A garantia é código.
- Comportamento visível ao paciente não muda: registro em silêncio.
- Decisão de dose fica com a médica; a Eva nunca confirma emissão de 20mg.
- Rodar `uv run pytest --tb=short`; todos os testes passam antes do PR.
- Trabalhar em worktree própria (`.worktrees/<branch>`), nunca na main.

## Review Focus

- Turno de handoff com pedido de emissão → quem registra é o `GUARD_DOC_PROMISE` (ramo existente); o guard novo NÃO pode disparar junto (dupla gravação). Coberto no Task 2.
- Modelo já chamou `request_document` no turno → guard novo não regrava. Coberto no Task 2.
- Reclamação clínica pura, sem palavra de documento → `_detect_document_emission_request` devolve None, não registra. Coberto no Task 2.
- Sem `patient_email` no state → não registra (pedir e-mail é do prompt). Coberto no Task 2.
- Refactor não pode mudar o retorno nem os efeitos da tool `request_document`. Coberto no Task 1.

---

### Task 1: Fatorar `request_document` em função reutilizável

**Files:**
- Modify: `app/graph/tools.py` (tool `request_document`, ~3009-3206)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: `async def register_document_request(document_type: str, patient_email: str, state: dict, config: RunnableConfig, medication_note: str = "", financial_name: str = "", financial_cpf: str = "", financial_email: str = "", patient_name_override: str = "") -> str` — faz todo o trabalho atual da tool (resolução de paciente, dedup, documents, evento, planilha, e-mails) e devolve a mensagem de sucesso/instrução. A `@tool request_document` passa a só `return await register_document_request(...)`.

- [ ] **Step 1: Escrever teste de delegação**

Em `tests/test_tools.py`, teste que `request_document` delega para `register_document_request`: com `monkeypatch` substituindo `register_document_request` por um stub que registra os argumentos e devolve `"OK_STUB"`, invocar a tool (via `.ainvoke` com state/config) e afirmar que o retorno é `"OK_STUB"` e que o stub recebeu `document_type`, `patient_email`, `state`, `config` e os demais parâmetros repassados.

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `uv run pytest tests/test_tools.py -k delega_register_document -v`
Expected: FAIL (ainda não existe `register_document_request`).

- [ ] **Step 3: Extrair a função**

Mover o corpo atual da tool (de "Fall back to state if LLM didn't pass medication_note" até o `return _document_success_message(...)` final) para `register_document_request(...)` com a assinatura do bloco Interfaces. A `@tool request_document` fica só com a docstring e `return await register_document_request(document_type, patient_email, state, config, medication_note, financial_name, financial_cpf, financial_email, patient_name_override)`. Comportamento idêntico.

- [ ] **Step 4: Rodar os testes de documento e ver passar**

Run: `uv run pytest tests/test_tools.py -k "document or receita or register_document" -v`
Expected: PASS (inclui os testes existentes de request_document, provando que o refactor não mudou comportamento).

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "refactor(tools): extrai register_document_request do request_document"
```

---

### Task 2: Guard de registro silencioso no patient_agent

**Files:**
- Modify: `app/graph/nodes.py` (`patient_agent_node`, logo após o bloco `GUARD_DOC_PROMISE`, ~3696)
- Test: `tests/test_process_message.py`

**Interfaces:**
- Consumes: `register_document_request(...)` do Task 1; `_detect_document_emission_request(text)` já existente em `nodes.py`.

- [ ] **Step 1: Escrever os testes do guard**

Em `tests/test_process_message.py`, com a LLM mockada para responder só texto (redirecionando a dose pro e-mail da médica, sem tool), e `register_document_request` mockado:
  - `test_guard_silent_registra_receita_com_dose`: mensagem do paciente "seria possível enviar uma receita com Escitalopram de 20mg? a de 10mg é baixa"; afirma que `register_document_request` foi chamado 1x com `document_type="receita"` e `medication_note` = mensagem do paciente, e que o texto final enviado ao paciente é o da Eva (inalterado, sem "registrei").
  - `test_guard_silent_ignora_reclamacao_clinica`: "o remédio tá me dando enjoo"; `register_document_request` não é chamado.
  - `test_guard_silent_nao_regrava_se_tool_chamada`: resposta do modelo já chama `request_document`; o guard não chama de novo (0 chamadas extras ao mock direto).
  - `test_guard_silent_pula_handoff`: resposta do modelo chama `transfer_to_human` + pedido de emissão; o guard novo não dispara (quem cobre é o GUARD_DOC_PROMISE).
  - `test_guard_silent_sem_email_nao_registra`: state sem `patient_email`; não registra.

- [ ] **Step 2: Rodar os testes e ver falhar**

Run: `uv run pytest tests/test_process_message.py -k guard_silent -v`
Expected: FAIL.

- [ ] **Step 3: Implementar o guard**

Em `patient_agent_node`, logo após o bloco `GUARD_DOC_PROMISE`, adicionar bloco `GUARD_DOC_EMISSION_SILENT`. Condição: `_last_h_idx is not None` e não `silent_mode`; a `response` não tem `request_document`/`nudge_doctor_document` em `tool_calls`; não `_doc_ran` (reusar do bloco anterior); não `_handoff_this_turn`; `_detect_document_emission_request(_patient_msg)` devolve tipo; `_doc_email` presente. Então `await register_document_request(document_type=<tipo>, patient_email=_doc_email, state=state, config=config, medication_note=_patient_msg if tipo=="receita" else "")`, dentro de try/except que loga `GUARD_DOC_EMISSION_SILENT` e segue. NÃO alterar `response` (silêncio). Logar com `_logger.warning`.

- [ ] **Step 4: Rodar os testes e ver passar**

Run: `uv run pytest tests/test_process_message.py -k guard_silent -v`
Expected: PASS.

- [ ] **Step 5: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(nodes): guard registra receita em silencio quando vem com duvida de dose"
```
