# confirm_appointment marca irmão certo em contato multi-paciente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que `confirm_appointment` grave uma consulta sob o irmão errado quando o contato administra vários pacientes, pedindo o nome completo em caso de dúvida.

**Architecture:** Uma rede de segurança no início de `confirm_appointment` (antes de qualquer escrita no Calendar/banco): para contatos com >1 paciente, só prossegue se `patient_name_override` singularizar exatamente um paciente via `_match_patient_by_name`; caso contrário retorna uma instrução interna pedindo o nome. Reusa as MESMAS funções (`get_users_by_phone`, `_match_patient_by_name`) que `_resolve_patient_for_booking`, garantindo paridade guard × insert. Complementada por uma instrução simétrica no prompt.

**Tech Stack:** Python, LangChain tools, pytest (mocks de Supabase/Calendar), OpenAI system prompt.

**Spec:** `docs/superpowers/specs/2026-08-14-confirm-appointment-irmaos-patient-override-design.md`

---

### Task 1: Rede de segurança no `confirm_appointment`

**Files:**
- Modify: `app/graph/tools.py` (inserir antes da linha 986, `_split_sibling: dict | None = None`)
- Test: `tests/test_tools.py` (reescrever `test_confirm_appointment_multi_patient_uses_user_db_id_over_stale_patient_name` na linha 787; adicionar 1 teste novo)

- [ ] **Step 1: Reescrever o teste que codifica o comportamento antigo**

Em `tests/test_tools.py`, substituir por completo a função `test_confirm_appointment_multi_patient_uses_user_db_id_over_stale_patient_name` (linhas ~787-814) por:

```python
async def test_confirm_appointment_multi_patient_empty_override_asks_for_name():
    """Contato com múltiplos pacientes e SEM patient_name_override: em vez de gravar no
    escuro pelo user_db_id/patient_name congelados (que causou o caso Renata/Laila+Suzi,
    5581996962165, 14/08/2026 — consulta pedida para Laila nasceu sob Suzi), a tool pede o
    nome completo e NÃO cria evento nem insere agendamento."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    create_event = AsyncMock(return_value="evt-should-not-happen")
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", create_event), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Laila Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
        )
    assert "NÃO ENVIE AO PACIENTE" in result
    assert "nome completo" in result.lower()
    assert not table.insert.called
    assert not create_event.called
```

- [ ] **Step 2: Adicionar teste do override que não singulariza (typo / ambíguo)**

Logo após a função reescrita, adicionar:

```python
async def test_confirm_appointment_multi_patient_nonunique_override_asks_for_name():
    """Override que não casa com exatamente um paciente (typo 'Layla', nome parcial, ou dois
    irmãos parecidos) NÃO pode cair no fallback user_db_id — _match_patient_by_name devolve
    None e a rede de segurança pede o nome, sem gravar. Trava o risco principal do design."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    create_event = AsyncMock(return_value="evt-should-not-happen")
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", create_event), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Suzi Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Layla",
        )
    assert "NÃO ENVIE AO PACIENTE" in result
    assert not table.insert.called
    assert not create_event.called
```

- [ ] **Step 3: Rodar os dois testes e verificar que FALHAM**

Run: `uv run pytest "tests/test_tools.py::test_confirm_appointment_multi_patient_empty_override_asks_for_name" "tests/test_tools.py::test_confirm_appointment_multi_patient_nonunique_override_asks_for_name" -v`
Expected: FAIL — hoje a tool grava o agendamento (`table.insert` é chamado / retorno é o AGENDAMENTO_OK, não a instrução interna).

- [ ] **Step 4: Implementar a rede de segurança**

Em `app/graph/tools.py`, inserir o bloco abaixo **imediatamente antes** da linha `_split_sibling: dict | None = None` (~986), no mesmo nível de indentação do corpo da função (4 espaços):

```python
    # ── Rede de segurança multi-paciente ──────────────────────────────────────
    # Para contatos que administram vários pacientes (irmãos no mesmo telefone), só
    # prosseguir se patient_name_override singularizar UM paciente. Sem override único,
    # _resolve_patient_for_booking cairia no user_db_id/patient_name congelados e gravaria
    # o irmão errado (caso Renata/Laila+Suzi, 5581996962165, 14/08/2026: consulta pedida
    # para Laila nasceu sob Suzi). _match_patient_by_name devolve None para override vazio,
    # typo ou nome que casa com >1 irmão — em todos esses casos pedimos o nome completo em
    # vez de agendar no escuro. Fica ANTES do create_event/insert (nunca cria evento sob o
    # irmão errado) e usa as MESMAS funções de _resolve_patient_for_booking (paridade).
    _phone_sn = config["configurable"]["phone"].replace("@s.whatsapp.net", "")
    try:
        _all_users_sn = await get_users_by_phone(_phone_sn)
    except Exception:
        # Supabase indisponível: não dá para avaliar multi-paciente aqui. Segue o fluxo
        # normal, que trata a falha de resolução adiante com rollback do evento do Calendar
        # (test_confirm_appointment_rolls_back_calendar_on_patient_resolution_failure).
        _all_users_sn = []
    if len(_all_users_sn) > 1 and _match_patient_by_name(_all_users_sn, patient_name_override) is None:
        _names_sn = ", ".join(
            u.get("patient_name") or u.get("name") or "Paciente" for u in _all_users_sn
        )
        _logger.warning(
            "confirm_appointment: contato multi-paciente sem override único — pedindo nome. phone=%s",
            _phone_sn,
        )
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Este contato administra mais de um "
            f"paciente ({_names_sn}) e não deu para identificar com segurança para qual a "
            "consulta é. Pergunte ao contato: 'Qual o nome completo do paciente para quem "
            "deseja agendar?' e rechame confirm_appointment com esse nome em "
            "patient_name_override."
        )
```

- [ ] **Step 5: Rodar os dois testes e verificar que PASSAM**

Run: `uv run pytest "tests/test_tools.py::test_confirm_appointment_multi_patient_empty_override_asks_for_name" "tests/test_tools.py::test_confirm_appointment_multi_patient_nonunique_override_asks_for_name" -v`
Expected: PASS (2 passed).

- [ ] **Step 5b: Atualizar teste existente que codificava o comportamento antigo**

A nova política (multi-paciente exige override) muda `test_guard_does_not_block_sibling_on_shared_phone`
(~linha 907), que hoje agenda um contato de 3 pacientes SEM override confiando no `state`. Ele deve
passar o override do irmão-alvo — a asserção do guard (mira o paciente certo, não bloqueia por causa
de outro irmão) continua válida. Adicionar `patient_name_override="Flavia Souza Passos"` na chamada
`confirm_appointment.coroutine(...)` dele (mantendo o resto igual) e acrescentar ao docstring a nota:
"Com a política de override obrigatório para contato multi-paciente, o nome do irmão-alvo vai em
patient_name_override; a asserção segue sendo que o guard mira exatamente esse paciente."

Run: `uv run pytest "tests/test_tools.py::test_guard_does_not_block_sibling_on_shared_phone" -v`
Expected: PASS.

- [ ] **Step 6: Rodar os testes de não-regressão (override único + paciente único)**

Run: `uv run pytest "tests/test_tools.py::test_confirm_appointment_multi_patient_override_beats_user_db_id" "tests/test_tools.py::test_confirm_appointment_matches_sibling_by_social_name" "tests/test_tools.py::test_confirm_appointment_creates_event_and_notifies" "tests/test_tools.py::test_confirm_appointment_insert_uses_patient_id_and_contact_id" -v`
Expected: PASS (4 passed) — override único ainda grava; contato de 1 paciente inalterado (get_users_by_phone retorna 1 → rede não dispara).

- [ ] **Step 7: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "fix(agendamento): pede nome do paciente quando contato multi-paciente não tem override único

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Instrução simétrica no prompt

**Files:**
- Modify: `app/graph/prompts.py` (bloco do passo de confirm_appointment, ~511-534)

- [ ] **Step 1: Ler o bloco atual para achar o ponto de inserção**

Run: `sed -n '505,536p' app/graph/prompts.py`
Expected: ver o passo "2. Chame confirm_appointment para registrar o agendamento" (linha ~533) e o CRÍTICO ANTI-LOOP (~529).

- [ ] **Step 2: Inserir a regra do override para multi-paciente**

Adicionar, imediatamente após a linha 533 (o passo "2. Chame confirm_appointment ... NUNCA UTC."), este parágrafo:

```
CRÍTICO — CONTATO COM VÁRIOS PACIENTES: se o telefone administra mais de um paciente (irmãos), \
chame confirm_appointment SEMPRE com patient_name_override = o nome exato que aparece no resumo \
"Paciente: ..." que você mostrou antes de confirmar — do mesmo jeito que já é exigido no \
register_payment. Se você não tiver certeza de qual paciente é (o contato não disse o nome, ou \
disse algo que não identifica um único), NÃO agende: pergunte "Qual o nome completo do paciente \
para quem deseja agendar?" e só então chame confirm_appointment com esse nome em \
patient_name_override.
```

- [ ] **Step 3: Rodar a suíte completa para garantir que nada quebrou**

Run: `uv run pytest --tb=short -q`
Expected: todos passam (o prompt é string; nenhum teste deve quebrar).

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "docs(prompt): exige patient_name_override no confirm_appointment para contato multi-paciente

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Regressão da 1ª consulta de menor dividida

**Files:**
- Test: `tests/test_tools.py` (adicionar após os testes da Task 1)

- [ ] **Step 1: Escrever o teste**

Adicionar em `tests/test_tools.py`:

```python
async def test_confirm_appointment_multi_patient_valid_override_with_session_note_inserts():
    """A 2ª sessão da 1ª consulta de menor dividida chama confirm_appointment de novo para o
    MESMO paciente, com session_note. Num contato multi-paciente, desde que o override do menor
    seja passado, a rede de segurança NÃO deve travar — o agendamento é inserido normalmente."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-split-2"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Suzi Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
            session_note="2ª hora — paciente",
            patient_name_override="Laila Monteiro Viana",
        )
    assert "NÃO ENVIE AO PACIENTE" not in result
    assert table.insert.called
    _insert_payload = table.insert.call_args[0][0]
    assert _insert_payload.get("patient_id") == "laila-id"
```

- [ ] **Step 2: Rodar o teste e verificar que PASSA**

Run: `uv run pytest "tests/test_tools.py::test_confirm_appointment_multi_patient_valid_override_with_session_note_inserts" -v`
Expected: PASS (a rede não dispara porque o override casa único; insert grava laila-id).

- [ ] **Step 3: Commit**

```bash
git add tests/test_tools.py
git commit -m "test(agendamento): 1ª consulta de menor dividida não é travada pela rede multi-paciente

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Verificação final

- [ ] **Step 1: Rodar a suíte completa**

Run: `uv run pytest --tb=short -q`
Expected: todos os testes passam (baseline era 181 em test_tools.py + demais arquivos).

- [ ] **Step 2: Conferir o diff**

Run: `git log --oneline main..HEAD` e `git diff main --stat`
Expected: 3 commits de implementação + o commit do spec; alterações só em `app/graph/tools.py`, `app/graph/prompts.py`, `tests/test_tools.py`, `docs/superpowers/`.
