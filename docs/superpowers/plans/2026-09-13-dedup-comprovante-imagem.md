# Dedup de comprovante de imagem — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que o mesmo comprovante de imagem, processado em dobro na corrida de turno, seja registrado duas vezes e gere duas mensagens de sucesso.

**Architecture:** Duas camadas. Na tool `register_payment`, uma guarda por `drive_link` mais janela de 30 minutos na tabela `events` que evita a segunda gravação e devolve um marcador. No `patient_agent_node`, uma guarda irmã do `GUARD_SLOT_TAKEN_VERBATIM` que detecta esse marcador e encerra o turno sem enviar mensagem ao paciente.

**Tech Stack:** Python, LangGraph, Supabase (tabela `events`), pytest com mock de Supabase.

---

## File Structure

- Modificar `app/graph/tools.py`: constante `RECEIPT_DEDUP_MARKER`, helper `_receipt_already_registered`, e a chamada da guarda dentro de `register_payment` logo após a resolução do `drive_link`.
- Modificar `app/graph/nodes.py`: guarda irmã no bloco de ToolMessages finais, encerrando o turno em silêncio ao ver o marcador.
- Modificar `tests/test_tools.py`: rotear `from_("events")` para uma tabela vazia no fixture compartilhado, e testes novos da camada 1.
- Modificar `tests/test_process_message.py`: testes novos da camada 2.

---

## Task 1: Guarda de dedup na tool register_payment

**Files:**
- Modify: `app/graph/tools.py` (constante e helper perto de `_BOOKING_FEE_RESEND_WINDOW_MINUTES:3364`; chamada da guarda após `app/graph/tools.py:3449`)
- Modify: `tests/test_tools.py` (fixture `_make_supabase_client_with_appointment:4038`; testes novos no fim do bloco register_payment)

- [ ] **Step 1: Rotear `from_("events")` para tabela vazia no fixture compartilhado**

Sem isto, a nova query de dedup cai no `side_effect` por contagem de chamadas do fixture e retorna dados de agendamento, quebrando os testes existentes de register_payment. Em `tests/test_tools.py`, dentro de `_make_supabase_client_with_appointment`, trocar o final da função (a partir de `execute = AsyncMock(side_effect=_side_effect)`) por:

```python
    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert", "is_"):
        getattr(table, m).return_value = table
    table.execute = execute

    # Tabela `events` isolada: a guarda de dedup lê daqui. Vazia por padrão, então
    # nenhum comprovante é tratado como duplicado nos testes que não configuram isto.
    events_table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "gte", "order"):
        getattr(events_table, m).return_value = events_table
    events_table.execute = AsyncMock(return_value=MagicMock(data=[]))

    client = MagicMock()

    def _from(name):
        return events_table if name == "events" else table
    client.from_.side_effect = _from
    return client, table, execute
```

- [ ] **Step 2: Rodar a suíte de register_payment para confirmar que o fixture novo não quebrou nada**

Run: `uv run pytest tests/test_tools.py -k register_payment -q`
Expected: PASS (mesmo número de testes de antes).

- [ ] **Step 3: Commit do preparo do fixture**

```bash
git add tests/test_tools.py
git commit -m "test(dedup): roteia from_(events) para tabela isolada no fixture de register_payment"
```

- [ ] **Step 4: Escrever o teste que falha — segundo comprovante com mesmo drive_link não grava**

Adicionar ao fim do bloco register_payment em `tests/test_tools.py`:

```python
def _client_with_appointment_and_event(event_rows):
    """Como _make_supabase_client_with_appointment, mas a tabela events devolve
    event_rows na query de dedup (lista de dicts, ex: [{'id': 'e1'}])."""
    client, table, execute = _make_supabase_client_with_appointment()
    events_table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "gte", "order"):
        getattr(events_table, m).return_value = events_table
    events_table.execute = AsyncMock(return_value=MagicMock(data=event_rows))
    def _from(name):
        return events_table if name == "events" else table
    client.from_.side_effect = _from
    return client, table, execute


async def test_register_payment_dedup_same_drive_link_skips_write():
    """Segundo processamento do MESMO comprovante (mesmo drive_link, <30min):
    não grava planilha, não grava evento, e devolve o marcador de dedup para o
    node suprimir a 2a mensagem. Corrida de processamento duplo — casos Silvia
    5581981179458 (11s) e Renato 34637036406 (52s)."""
    from app.graph.tools import register_payment, RECEIPT_DEDUP_MARKER
    client, table, execute = _client_with_appointment_and_event([{"id": "e-prev"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )

    assert RECEIPT_DEDUP_MARKER in result
    mock_sheets.assert_not_called()
    mock_log.assert_not_called()
    assert not table.insert.called
    assert not table.update.called
```

- [ ] **Step 5: Rodar o teste e confirmar que falha**

Run: `uv run pytest tests/test_tools.py::test_register_payment_dedup_same_drive_link_skips_write -q`
Expected: FAIL com `ImportError` de `RECEIPT_DEDUP_MARKER` (ainda não existe).

- [ ] **Step 6: Implementar a constante e o helper**

Em `app/graph/tools.py`, logo após a linha `_BOOKING_FEE_RESEND_WINDOW_MINUTES = 30` (tools.py:3364), adicionar:

```python
# Marcador devolvido por register_payment quando o MESMO comprovante de imagem já
# foi registrado há pouco (drive_link idêntico dentro da janela de reenvio). O
# patient_agent_node detecta este marcador e encerra o turno em silêncio, para o
# paciente receber uma única mensagem de sucesso. É a corrida de processamento
# duplo do turno: mesmo arquivo, mesmos segundos, mesmo drive_link (casos Silvia
# 5581981179458 11s, Renato 34637036406 52s). NÃO cobre reenvio em outro dia
# (arquivo novo, drive_link diferente) — isso não é rajada e é tratado por outros
# guards (paid_at) ou pela atendente.
RECEIPT_DEDUP_MARKER = "[COMPROVANTE_JA_REGISTRADO]"


async def _receipt_already_registered(client, phone: str, drive_link: str) -> bool:
    """True se já existe um payment_receipt_registered com este drive_link, para
    as variantes deste telefone, dentro de _BOOKING_FEE_RESEND_WINDOW_MINUTES.

    Espelha o dedup de request_document (tools.py request_document). Só faz sentido
    para comprovante de imagem: lançamentos do painel (is_link/payment_method) não
    têm drive_link e nunca chegam aqui."""
    from datetime import timezone as _tz
    _cutoff = (datetime.now(_tz.utc) - timedelta(minutes=_BOOKING_FEE_RESEND_WINDOW_MINUTES)).isoformat()
    _recent = await client.from_("events").select("id") \
        .eq("event_type", "payment_receipt_registered") \
        .eq("metadata->>drive_link", drive_link) \
        .in_("phone", _phone_variants(phone)) \
        .gte("created_at", _cutoff) \
        .limit(1).execute()
    return bool(_recent.data)
```

- [ ] **Step 7: Chamar a guarda dentro de register_payment**

Em `app/graph/tools.py`, logo após o bloco `_logger.info("REGISTER_PAYMENT start: ...")` (que termina em tools.py:3449) e antes do comentário `# ── Resolve patient ──`, inserir:

```python
    # ── Guard: mesmo comprovante de imagem processado em dobro ─────────────────
    # A corrida de processamento duplo do turno chama register_payment duas vezes
    # com o MESMO drive_link em segundos. Sem esta guarda o pagamento é gravado
    # duas vezes (planilha + evento) e o paciente recebe duas mensagens de sucesso.
    # Só imagem: is_link/payment_method (painel) não têm drive_link. Reenvio em
    # outro dia gera arquivo novo (drive_link diferente) e não é pego aqui — é o
    # comportamento certo, pois é um envio separado que merece resposta própria.
    if drive_link and not is_link and not payment_method:
        if await _receipt_already_registered(client, phone, drive_link):
            _logger.warning(
                "REGISTER_PAYMENT dedup: mesmo drive_link em <%dmin — não registra de novo. phone=%s link=%s",
                _BOOKING_FEE_RESEND_WINDOW_MINUTES, phone, drive_link,
            )
            return (
                f"{RECEIPT_DEDUP_MARKER}\n"
                "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Este comprovante já foi "
                "registrado há instantes (processamento duplo). Nada foi gravado de novo."
            )
```

- [ ] **Step 8: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_tools.py::test_register_payment_dedup_same_drive_link_skips_write -q`
Expected: PASS

- [ ] **Step 9: Escrever os testes negativos — não deve deduplicar o que é legítimo**

Adicionar em `tests/test_tools.py`:

```python
async def test_register_payment_different_drive_link_registers_normally():
    """Comprovante com drive_link DIFERENTE (não é a mesma imagem) grava normal.
    events vazio para este link → sem dedup."""
    from app.graph.tools import register_payment, RECEIPT_DEDUP_MARKER
    client, table, execute = _client_with_appointment_and_event([])  # nenhum evento prévio
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/NOVO/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert RECEIPT_DEDUP_MARKER not in result
    assert "✅" in result
    mock_sheets.assert_awaited_once()


async def test_register_payment_panel_link_never_dedups():
    """Lançamento do painel (is_link=True, sem drive_link) nunca entra na guarda de
    dedup, mesmo que a tabela events tivesse algo — a guarda exige drive_link."""
    from app.graph.tools import register_payment, RECEIPT_DEDUP_MARKER
    client, table, execute = _client_with_appointment_and_event([{"id": "e-prev"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="600,00",
            drive_link="",
            state=_make_state(),
            config=CONFIG,
            is_link=True,
        )
    assert RECEIPT_DEDUP_MARKER not in result
    mock_sheets.assert_awaited_once()
```

- [ ] **Step 10: Rodar os testes negativos**

Run: `uv run pytest tests/test_tools.py -k "dedup or different_drive_link or panel_link_never" -q`
Expected: PASS (3 testes).

- [ ] **Step 11: Rodar toda a suíte de register_payment para garantir zero regressão**

Run: `uv run pytest tests/test_tools.py -k register_payment -q`
Expected: PASS

- [ ] **Step 12: Commit da camada 1**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(dedup): register_payment ignora reenvio do mesmo comprovante de imagem em <30min"
```

---

## Task 2: Supressão da segunda mensagem no patient_agent_node

**Files:**
- Modify: `app/graph/nodes.py` (bloco de ToolMessages finais, junto do `GUARD_SLOT_TAKEN_VERBATIM:2039-2072`)
- Modify: `tests/test_process_message.py` (testes novos no fim do arquivo)

- [ ] **Step 1: Escrever o teste que falha — resultado com marcador encerra o turno sem enviar nada**

Adicionar ao fim de `tests/test_process_message.py`:

```python
async def test_receipt_dedup_marker_ends_turn_without_message():
    """Quando register_payment devolve o marcador de dedup (2o processamento do
    mesmo comprovante), o node encerra o turno sem enviar mensagem ao paciente.
    Assim o paciente recebe uma única confirmação, a do 1o processamento."""
    from app.graph.nodes import patient_agent_node
    from app.graph.tools import RECEIPT_DEDUP_MARKER
    from langchain_core.messages import ToolMessage

    ai_with_call = AIMessage(content="")
    ai_with_call.tool_calls = [{"name": "register_payment", "args": {}, "id": "tc_rp", "type": "tool_call"}]
    tool_msg = ToolMessage(
        content=f"{RECEIPT_DEDUP_MARKER}\n[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] duplicado.",
        tool_call_id="tc_rp",
        name="register_payment",
    )

    state = _make_patient_agent_state(
        messages=[
            HumanMessage(content="[imagem]: COMPROVANTE ..."),
            ai_with_call,
            tool_msg,
        ],
    )

    sent = []
    async def fake_send_text(phone, text):
        sent.append(text)

    with patch("app.graph.nodes.send_text", side_effect=fake_send_text), \
         patch("app.whatsapp.send_text", side_effect=fake_send_text), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None):
        result = await patient_agent_node(state, CONFIG)

    assert sent == [], f"nenhuma mensagem deve ir ao paciente no 2o processamento; foi: {sent!r}"
    mock_save.assert_not_called()
    # o turno encerra: última mensagem é AIMessage sem tool_calls
    last = result["messages"][-1]
    assert getattr(last, "tool_calls", None) in (None, [])
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `uv run pytest tests/test_process_message.py::test_receipt_dedup_marker_ends_turn_without_message -q`
Expected: FAIL — hoje o node tenta chamar a LLM (ou segue o fluxo) e não encerra em silêncio.

- [ ] **Step 3: Implementar a guarda irmã no node**

Em `app/graph/nodes.py`, no bloco de ToolMessages finais, a importação do marcador de slot-taken está em nodes.py:2039. Trocar essa linha por:

```python
    from app.graph.tools import REACTIVATION_SLOT_TAKEN_MARKER as _SLOT_TAKEN_MARKER
    from app.graph.tools import RECEIPT_DEDUP_MARKER as _RECEIPT_DEDUP_MARKER
```

Depois, dentro do `for _tm_rp in _trailing_tools:`, logo antes do `if _tm_name == "register_payment" and _SLOT_TAKEN_MARKER in _tm_content:`, inserir:

```python
            if _tm_name == "register_payment" and _RECEIPT_DEDUP_MARKER in _tm_content:
                # 2o processamento do mesmo comprovante (dedup na tool). Nada foi
                # gravado de novo; encerrar o turno em silêncio para o paciente não
                # receber uma 2a mensagem de sucesso. AIMessage vazio fecha o turno
                # (routing vai para END: sem tool_calls e sem RESUME_AFTER_TOOL).
                _pa_logger.info(
                    "GUARD_RECEIPT_DEDUP: comprovante duplicado, encerrando turno em silêncio phone=%s",
                    state.get("phone"),
                )
                return {"messages": [AIMessage(content="")], "silent_mode": False}
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_process_message.py::test_receipt_dedup_marker_ends_turn_without_message -q`
Expected: PASS

- [ ] **Step 5: Escrever o teste de não-regressão — resultado normal ainda envia mensagem**

Adicionar em `tests/test_process_message.py`:

```python
async def test_register_payment_normal_result_still_replies():
    """Resultado normal de register_payment (sem o marcador de dedup) NÃO é
    suprimido: o fluxo segue e a LLM responde ao paciente como hoje."""
    from app.graph.nodes import patient_agent_node
    from langchain_core.messages import ToolMessage

    ai_with_call = AIMessage(content="")
    ai_with_call.tool_calls = [{"name": "register_payment", "args": {}, "id": "tc_rp", "type": "tool_call"}]
    tool_msg = ToolMessage(
        content="Comprovante recebido e registrado com sucesso! ✅",
        tool_call_id="tc_rp",
        name="register_payment",
    )
    state = _make_patient_agent_state(
        messages=[HumanMessage(content="[imagem]: COMPROVANTE ..."), ai_with_call, tool_msg],
    )

    final = AIMessage(content="Perfeito! Comprovante registrado ✅")
    async def fake_ainvoke(messages):
        return final
    sent = []
    async def fake_send_text(phone, text):
        sent.append(text)

    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", side_effect=fake_send_text), \
         patch("app.whatsapp.send_text", side_effect=fake_send_text), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        await patient_agent_node(state, CONFIG)

    assert any("registrado" in t for t in sent), f"resultado normal deve responder ao paciente; enviado: {sent!r}"
```

- [ ] **Step 6: Rodar os dois testes do node**

Run: `uv run pytest tests/test_process_message.py -k "receipt_dedup_marker or register_payment_normal_result" -q`
Expected: PASS (2 testes).

- [ ] **Step 7: Commit da camada 2**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "feat(dedup): patient_agent_node suprime 2a mensagem de comprovante duplicado"
```

---

## Task 3: Verificação final

- [ ] **Step 1: Rodar a suíte completa**

Run: `uv run pytest --tb=short -q`
Expected: PASS (todos, incluindo os 5 testes novos).

- [ ] **Step 2: Abrir o PR**

```bash
git push -u origin dedup-comprovante-imagem
gh pr create --base main --head dedup-comprovante-imagem \
  --title "feat(dedup): comprovante de imagem processado em dobro não registra nem responde duas vezes" \
  --body "Ver docs/superpowers/specs/2026-09-13-dedup-comprovante-imagem-design.md"
```

---

## Self-review (preenchido pelo autor do plano)

Cobertura do spec: camada 1 (dedup no dado) = Task 1; camada 2 (supressão da mensagem) = Task 2; escopo só-imagem = Steps 7/9 da Task 1; janela de 30min reusando `_BOOKING_FEE_RESEND_WINDOW_MINUTES` = Step 6 da Task 1; painel não deduplica = Step 9 da Task 1. Reenvio em outro dia e painel ficam fora, conforme spec.

Consistência de tipos: `RECEIPT_DEDUP_MARKER` (constante str) e `_receipt_already_registered(client, phone, drive_link) -> bool` usados com a mesma assinatura na tool e importados por nome no node. O marcador é o mesmo símbolo nas duas camadas.

Sem placeholders: todo passo que muda código traz o código completo.
