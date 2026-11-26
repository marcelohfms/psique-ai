# Cadastro de paciente gerenciado pelo chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que um contato que já é paciente cadastre pelo chat um paciente ADULTO que ele gerencia no mesmo número e agende para ele, sem a consulta cair na ficha errada.

**Architecture:** Uma trava determinística na `confirm_appointment` barra agendar numa ficha existente quando o nome informado não tem cadastro exato naquele número; uma ferramenta nova `register_managed_patient` cria a ficha do novo paciente (ou reconcilia retornante), vincula ao contato e devolve o nome exato para a Eva rechamar a `confirm_appointment`; uma regra de prompt ensina a Eva a usar o fluxo. Menor de 18 dispara transferência determinística.

**Tech Stack:** Python, FastAPI, LangGraph, LangChain tools, Supabase (async), pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-cadastro-paciente-gerenciado-pelo-chat-design.md`

## Global Constraints

- Toda a implementação acontece numa git worktree própria (`.worktrees/<branch>`), nunca na `main` (CLAUDE.md).
- Testes rodam com `uv run pytest --tb=short`; todos têm que passar antes de mergear.
- Testes novos vão em `tests/test_tools.py` (trava e ferramenta) e `tests/test_prompts.py` ou `tests/test_process_message.py` (regra de prompt), seguindo a estrutura por camada do projeto (CLAUDE.md).
- Match de nome para a pergunta "tem ficha?" é EXATO e normalizado com `app.patients.normalize_person_name` (sem acento, minúsculo); nunca substring.
- v1 adulto-só: idade < 18 nunca cria ficha; dispara transferência.
- Regra de ouro: na dúvida, travar (falso bloqueio é barato; agendar no paciente errado é caro).
- Commits terminam com `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Review Focus

- Override que é prefixo/substring do nome de uma ficha existente (pai "Helder Siqueira Maciel" x filho "Helder Siqueira Maciel Júnior") tem que BLOQUEAR, não agendar → Task 1.
- Override vazio (autoagendamento normal do próprio paciente) NÃO pode ser afetado pela trava → Task 1.
- Variação de acento entre override e ficha ("José" x "Jose") tem que casar no match exato (fold) → Task 1.
- Menor gerenciado tem que transferir de forma determinística e NUNCA criar ficha → Task 2.
- Retornante que já existe sob outro número tem que reconciliar por nome + nascimento, não duplicar → Task 2.

---

### Task 1: Trava determinística na confirm_appointment + helper de match exato

**Files:**
- Modify: `app/graph/tools.py` (novo helper `_exact_patient_match`; nova verificação dentro de `confirm_appointment`, perto da rede multi-paciente em `~1200-1226`)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: `_exact_patient_match(all_users: list[dict], name: str) -> dict | None` — devolve a ficha cujo `patient_name`/`name` ou `social_name` casa EXATAMENTE com `name` após `normalize_person_name`; senão `None`.
- Consumes: `get_users_by_phone(phone)` (já importado), `app.patients.normalize_person_name`.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_exact_patient_match_ignores_prefix_and_folds_accents():
    son = {"patient_name": "Helder Siqueira Maciel Júnior", "social_name": None}
    jose = {"patient_name": "José da Silva", "social_name": None}
    assert _exact_patient_match([son], "Helder Siqueira Maciel") is None   # prefixo não casa
    assert _exact_patient_match([son], "helder siqueira maciel júnior") == son
    assert _exact_patient_match([jose], "Jose da Silva") == jose           # acento normalizado

@pytest.mark.asyncio
async def test_confirm_blocks_override_for_unregistered_person():
    # Telefone com UMA ficha (o filho). Override nomeia o pai (sem ficha).
    son = {"id": "son-id", "patient_name": "Helder Siqueira Maciel Júnior"}
    with patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[son]), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        out = await confirm_appointment.coroutine(
            "2026-12-02T11:00:00", 60, _make_state(preferred_doctor="bruna"),
            CONFIG, patient_name_override="Helder Siqueira Maciel")
    assert "register_managed_patient" in out
    mock_create.assert_not_called()

@pytest.mark.asyncio
async def test_confirm_does_not_block_when_override_matches_exactly():
    son = {"id": "son-id", "patient_name": "Helder Siqueira Maciel Júnior"}
    with patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[son]), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt") as mock_create, \
         patch("app.graph.tools._resolve_patient_for_booking", new_callable=AsyncMock, return_value=son), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=_make_supabase_client()):
        out = await confirm_appointment.coroutine(
            "2026-12-02T11:00:00", 60, _make_state(preferred_doctor="bruna"),
            CONFIG, patient_name_override="Helder Siqueira Maciel Júnior")
    assert "register_managed_patient" not in out
    mock_create.assert_called_once()

@pytest.mark.asyncio
async def test_confirm_empty_override_single_patient_not_blocked():
    # Autoagendamento normal: override vazio nunca cai na trava.
    me = {"id": "me-id", "patient_name": "Maria"}
    with patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[me]), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt") as mock_create, \
         patch("app.graph.tools._resolve_patient_for_booking", new_callable=AsyncMock, return_value=me), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=_make_supabase_client()):
        out = await confirm_appointment.coroutine(
            "2026-12-02T11:00:00", 60, _make_state(), CONFIG, patient_name_override="")
    assert "register_managed_patient" not in out
    mock_create.assert_called_once()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py -k "exact_patient_match or override_for_unregistered or override_matches_exactly or empty_override_single" -v`
Expected: FAIL (`_exact_patient_match` não existe; trava ainda não bloqueia).

- [ ] **Step 3: Implementar o helper e a trava**

Em `app/graph/tools.py`, adicionar `_exact_patient_match` (três campos: `patient_name`, `name`, `social_name`; compara com `normalize_person_name`). Dentro de `confirm_appointment`, logo após montar `_all_users_sn` (perto da rede multi-paciente, `~1200-1226`) e ANTES de qualquer `create_event`/insert: se `patient_name_override.strip()` e `_exact_patient_match(_all_users_sn, patient_name_override) is None`, devolver instrução interna:

```
"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] O paciente '<override>' ainda não "
"tem cadastro neste número. NÃO agende na ficha existente. Se for paciente ADULTO, "
"colete nome completo, data de nascimento, se já é paciente da clínica, CPF e e-mail "
"e chame register_managed_patient; depois rechame confirm_appointment com esse nome "
"em patient_name_override. Se for MENOR de 18, chame register_managed_patient mesmo "
"assim — ela transfere para a atendente."
```

Manter intacta a rede multi-paciente que já existe.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k "exact_patient_match or override_for_unregistered or override_matches_exactly or empty_override_single" -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte da confirm_appointment para garantir que nada regrediu**

Run: `uv run pytest tests/test_tools.py -k confirm -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(agendamento): trava confirm_appointment contra agendar em ficha de outra pessoa"
```

---

### Task 2: Ferramenta register_managed_patient

**Files:**
- Modify: `app/graph/tools.py` (nova tool `register_managed_patient`)
- Modify: `app/graph/nodes.py` (incluir a tool em `TOOLS`, linha ~33)
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: tool `register_managed_patient(patient_name: str, birth_date: str, is_returning_patient: bool, patient_cpf: str, patient_email: str, relationship: str, state, config) -> str`. Em sucesso devolve string começando com `PACIENTE_CADASTRADO:` seguida do nome exato gravado. Em menor, dispara transferência e devolve a mensagem do handoff. Em nome inválido, pede o nome de novo.
- Consumes: `app.utils.looks_like_name`, `app.utils.format_person_name`, parser de data + `_compute_age` de `app.graph.nodes` (import tardio dentro da função para evitar import circular), `app.database.get_contact_by_phone`, `app.patients.upsert_patient`, `app.database.link_patient_contact`, `transfer_to_human.coroutine` (Task existente).

- [ ] **Step 1: Escrever os testes que falham**

```python
@pytest.mark.asyncio
async def test_register_managed_patient_creates_ficha_and_links_three_roles():
    contact = {"id": "contact-id"}
    with patch("app.graph.tools.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.graph.tools.upsert_patient", new_callable=AsyncMock, return_value="dad-id") as mock_up, \
         patch("app.graph.tools.link_patient_contact", new_callable=AsyncMock) as mock_link:
        out = await register_managed_patient.coroutine(
            "Helder Siqueira Maciel", "10/03/1960", False, "123.456.789-00",
            "pai@x.com", "pai", _make_state(), CONFIG)
    assert out.startswith("PACIENTE_CADASTRADO:")
    assert "Helder Siqueira Maciel" in out
    # insert forçado (sem patient_id): upsert_patient chamado com name+birth_date
    args, kwargs = mock_up.call_args
    assert args[0]["birth_date"] == "10/03/1960"
    # três papéis, todos is_self=False
    roles = {c.args[2] for c in mock_link.call_args_list}
    assert roles == {"agendamento", "financeiro", "consulta"}
    assert all(c.kwargs.get("is_self") is False for c in mock_link.call_args_list)

@pytest.mark.asyncio
async def test_register_managed_patient_minor_transfers_and_creates_nothing():
    with patch("app.graph.tools.upsert_patient", new_callable=AsyncMock) as mock_up, \
         patch("app.graph.tools.transfer_to_human") as mock_transfer:
        mock_transfer.coroutine = AsyncMock(return_value="transferido")
        out = await register_managed_patient.coroutine(
            "Joãozinho Silva", "10/03/2015", False, "", "", "filho", _make_state(), CONFIG)
    mock_up.assert_not_called()
    mock_transfer.coroutine.assert_awaited_once()

@pytest.mark.asyncio
async def test_register_managed_patient_rejects_non_name():
    with patch("app.graph.tools.upsert_patient", new_callable=AsyncMock) as mock_up:
        out = await register_managed_patient.coroutine(
            "quinta-feira à tarde", "10/03/1960", False, "", "", "pai", _make_state(), CONFIG)
    mock_up.assert_not_called()
    assert "nome" in out.lower()

@pytest.mark.asyncio
async def test_register_managed_patient_returning_relies_on_reconciliation():
    # Retornante: a tool chama upsert_patient SEM patient_id (nunca resolve por
    # telefone), deixando a dedup por nome+nascimento do upsert_patient agir.
    contact = {"id": "contact-id"}
    with patch("app.graph.tools.get_contact_by_phone", new_callable=AsyncMock, return_value=contact), \
         patch("app.graph.tools.upsert_patient", new_callable=AsyncMock, return_value="existing-id") as mock_up, \
         patch("app.graph.tools.link_patient_contact", new_callable=AsyncMock):
        out = await register_managed_patient.coroutine(
            "Helder Siqueira Maciel", "10/03/1960", True, "", "pai@x.com", "pai",
            _make_state(), CONFIG)
    assert out.startswith("PACIENTE_CADASTRADO:")
    # upsert_patient chamado posicionalmente só com os dados (sem patient_id)
    assert mock_up.call_args.kwargs.get("patient_id") in (None, )
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_tools.py -k register_managed_patient -v`
Expected: FAIL (`register_managed_patient` não existe).

- [ ] **Step 3: Implementar a tool**

Ordem interna: (1) `if not looks_like_name(patient_name): return` reperguntar o nome. (2) calcular idade com o mesmo parser/`_compute_age` do `collect_info` (import tardio de `app.graph.nodes`); se idade < 18, `await transfer_to_human.coroutine(reason="Cadastro de paciente menor gerenciado — v1 adulto-só", state=state, config=config)` e devolver a mensagem; NÃO criar ficha. (3) `contact = await get_contact_by_phone(<phone do config>)`. (4) `patient_id = await upsert_patient({"name": format_person_name(patient_name), "birth_date": birth_date, "cpf": patient_cpf or None, "email": patient_email or None})` — sem `patient_id`, então o `upsert_patient` reconcilia retornante por nome+nascimento ou insere ficha nova, nunca resolve por telefone. (5) vincular os três papéis `agendamento`/`financeiro`/`consulta` com `is_self=False, relationship=relationship`. (6) devolver `f"PACIENTE_CADASTRADO: {nome_exato}. ..."`. CPF: exigir só quando `is_returning_patient` for False; retornante pode vir sem CPF (a clínica já tem).

- [ ] **Step 4: Adicionar a tool em TOOLS**

Em `app/graph/nodes.py` (~linha 33), incluir `register_managed_patient` na lista `TOOLS` e no import de `app.graph.tools`.

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_tools.py -k register_managed_patient -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py app/graph/nodes.py tests/test_tools.py
git commit -m "feat(cadastro): tool register_managed_patient para paciente adulto gerenciado"
```

---

### Task 3: Regra de prompt para a Eva usar o fluxo

**Files:**
- Modify: `app/graph/prompts.py` (regra no bloco do agente de paciente conhecido, junto do passo de agendamento ~678-685)
- Test: `tests/test_prompts.py` (criar se não existir) ou `tests/test_process_message.py`

**Interfaces:**
- Consumes: a trava (Task 1) e a tool (Task 2).

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_existing_patient_prompt_has_managed_patient_rule():
    from app.graph.prompts import EXISTING_PATIENT_SYSTEM  # ou o builder do prompt do agente
    text = EXISTING_PATIENT_SYSTEM if isinstance(EXISTING_PATIENT_SYSTEM, str) else str(EXISTING_PATIENT_SYSTEM)
    assert "register_managed_patient" in text
    assert "menor" in text.lower()  # instrui transferir menor
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_prompts.py -k managed_patient_rule -v`
Expected: FAIL

- [ ] **Step 3: Adicionar a regra no prompt**

No bloco do agendamento do paciente conhecido, acrescentar: quando o contato quiser agendar ou adiantar pagamento para alguém que ainda não é paciente cadastrado naquele número, colete nome completo, data de nascimento, se já é paciente da clínica, CPF e e-mail, chame `register_managed_patient` e só então `confirm_appointment` com o nome do novo paciente em `patient_name_override`; se for menor de 18, chame `register_managed_patient` do mesmo jeito, que ela transfere para a atendente. Confirmar em qual(is) bloco(s) a regra precisa aparecer (há blocos espelhados em ~1311 e ~1731).

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_prompts.py -k managed_patient_rule -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/prompts.py tests/test_prompts.py
git commit -m "feat(prompt): Eva cadastra paciente adulto gerenciado antes de agendar"
```

---

### Task 4: Suíte completa verde

- [ ] **Step 1: Rodar tudo**

Run: `uv run pytest --tb=short`
Expected: PASS (nada regrediu).

- [ ] **Step 2: Commit de ajustes se algum teste pré-existente precisou mudar**

```bash
git add -A
git commit -m "test: ajustes para cadastro de paciente gerenciado"
```
