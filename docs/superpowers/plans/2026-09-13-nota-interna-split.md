# Nota interna que vaza — Plano de implementação

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. TDD, um commit ao fim.

**Goal:** Dividir a mensagem no marcador de nota interna em qualquer posição: parte antes vai ao paciente, parte do marcador em diante vira nota privada. Hoje só detecta o marcador no início (`startswith`) e o resto vaza.

**Architecture:** Trocar a detecção por `startswith` em `app/graph/nodes.py` por uma busca do primeiro marcador em qualquer posição, e dividir o envio.

**Tech Stack:** Python, LangGraph, pytest.

---

## File Structure

- Modify: `app/graph/nodes.py` (bloco de envio final, nodes.py:3227-3271)
- Modify: `tests/test_process_message.py` (testes novos no fim)

---

## Task 1: Dividir a mensagem no marcador

**Files:**
- Modify: `app/graph/nodes.py:3230-3271`
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao fim de `tests/test_process_message.py`:

```python
async def _run_agent_note_split(ai_content, conv_id="conv-1"):
    """Roda patient_agent_node com resposta de texto puro e captura send_text e
    add_private_note. Retorna (sent, notes)."""
    from app.graph.nodes import patient_agent_node
    state = _make_patient_agent_state(messages=[HumanMessage(content="oi")])
    ai_response = AIMessage(content=ai_content)
    async def fake_ainvoke(messages):
        return ai_response
    sent, notes = [], []
    async def fake_send_text(phone, text):
        sent.append(text)
    async def fake_add_note(cid, text):
        notes.append(text)
    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", side_effect=fake_send_text), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_conversation_id", return_value=conv_id), \
         patch("app.graph.nodes.add_private_note", side_effect=fake_add_note), \
         _GUARD_RECEIPT_PATCHES["get_upcoming_appointments"], \
         _GUARD_RECEIPT_PATCHES["get_user_by_phone"], \
         _GUARD_RECEIPT_PATCHES["get_last_assistant_message_time"], \
         _GUARD_RECEIPT_PATCHES["format_doctor_schedules"]:
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        await patient_agent_node(state, CONFIG)
    return sent, notes


async def test_internal_note_mid_message_splits_patient_and_team():
    """Caso Wayne (5581999597907, 08/09/2026): frase de paciente + nota interna na
    mesma mensagem. A parte antes do marcador vai ao paciente; a nota vira nota
    privada; o texto da nota NÃO chega ao paciente."""
    content = (
        "Os valores das consultas foram atualizados em junho de 2026, tá bom? 😊\n\n"
        "Nota para a equipe: segundo a Dra. Bruna, Wayne não está mais em acompanhamento."
    )
    sent, notes = await _run_agent_note_split(content)

    assert len(sent) == 1
    assert "valores das consultas foram atualizados" in sent[0]
    assert "Nota para a equipe" not in sent[0], "nota vazou para o paciente"
    assert "não está mais em acompanhamento" not in sent[0]
    assert len(notes) == 1
    assert "não está mais em acompanhamento" in notes[0]


async def test_internal_note_at_start_only_private_note():
    """Marcador no início: nada vai ao paciente, tudo vira nota privada (igual a hoje)."""
    sent, notes = await _run_agent_note_split("Nota para a equipe: verificar X com o Dr. Júlio.")
    assert sent == []
    assert len(notes) == 1
    assert "verificar X" in notes[0]


async def test_no_marker_goes_to_patient():
    """Sem marcador: tudo vai ao paciente, nada vira nota privada."""
    sent, notes = await _run_agent_note_split("Perfeito! Sua consulta está confirmada. 😊")
    assert len(sent) == 1
    assert "consulta está confirmada" in sent[0]
    assert notes == []


async def test_internal_note_fallback_to_whatsapp_without_conv_id():
    """Fallback preservado: sem conv_id, a nota cai para o WhatsApp (melhor lugar
    errado do que sumir). Só a parte da nota; a parte de paciente também é enviada."""
    content = "Confirmado! 😊\n\nNota para a equipe: conferir cadastro."
    sent, notes = await _run_agent_note_split(content, conv_id=None)
    assert notes == []  # add_private_note nunca chamado (sem conv_id)
    # parte do paciente + parte da nota, ambas via send_text
    assert any("Confirmado" in s for s in sent)
    assert any("conferir cadastro" in s for s in sent)
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `uv run pytest tests/test_process_message.py -k "internal_note or no_marker_goes" -q`
Expected: FAIL (hoje o marcador no meio manda tudo ao paciente).

- [ ] **Step 3: Implementar a divisão**

Em `app/graph/nodes.py`, substituir o bloco atual (de `# Detect internal-note responses by content prefix ONLY.` até o fim do `else:` com o `needs_price_notice`, hoje nodes.py:3230-3271) por:

```python
        # Detecta nota interna em QUALQUER posição do texto (não só no início).
        # A Eva às vezes gruda uma frase de paciente antes do marcador (caso Wayne
        # 5581999597907, 08/09/2026: aviso de preço + "Nota para a equipe: ..."),
        # e o startswith antigo deixava a nota vazar inteira para o paciente.
        # Dividimos: parte ANTES do marcador vai ao paciente; do marcador em diante
        # vira nota privada. Prompt regra (b): o marcador é APENAS para a equipe.
        _INTERNAL_PREFIXES = (
            "nota para a equipe",
            "nota interna",
            "nota para equipe",
            "[nota interna]",
            "[nota para a equipe]",
        )
        _content = response.content
        _lower = _content.lower()
        _marker_idx = min(
            (_lower.find(p) for p in _INTERNAL_PREFIXES if p in _lower),
            default=-1,
        )

        if _marker_idx >= 0:
            _patient_part = _content[:_marker_idx].strip()
            _note_part = _content[_marker_idx:].strip()

            # Nota (marcador em diante) → nota privada no Chatwoot.
            # Fallback: sem conv_id ou falha da API, cai para o WhatsApp — melhor a
            # nota chegar no lugar errado do que sumir.
            import logging as _log
            _node_logger = _log.getLogger(__name__)
            conv_id = get_conversation_id(phone)
            _node_logger.info("PRIVATE_NOTE attempt phone=%s conv_id=%s", phone, conv_id)
            posted = False
            if conv_id:
                try:
                    await add_private_note(conv_id, _note_part)
                    posted = True
                except Exception:
                    _node_logger.exception(
                        "PRIVATE_NOTE FAILED phone=%s conv_id=%s — falling back to WhatsApp", phone, conv_id
                    )
            if not posted:
                _node_logger.warning(
                    "PRIVATE_NOTE no conv_id or post failed — sending note to WhatsApp phone=%s", phone
                )
                await send_text(phone, _note_part)

            # Parte antes do marcador (se houver) → paciente.
            if _patient_part:
                await send_text(phone, _patient_part)
                if needs_price_notice:
                    await upsert_user(phone, {"price_adjustment_notified_at": now_dt.isoformat()}, user_id=state.get("user_db_id"))

            # Histórico fiel: grava o conteúdo completo uma vez.
            await save_message(phone, "assistant", _content)
        else:
            await send_text(phone, _content)
            await save_message(phone, "assistant", _content)
            if needs_price_notice:
                await upsert_user(phone, {"price_adjustment_notified_at": now_dt.isoformat()}, user_id=state.get("user_db_id"))
```

- [ ] **Step 4: Rodar os testes novos e confirmar PASS**

Run: `uv run pytest tests/test_process_message.py -k "internal_note or no_marker_goes" -q`
Expected: PASS (4 testes).

- [ ] **Step 5: Suíte completa**

Run: `uv run pytest -q`
Expected: PASS, zero regressão.

- [ ] **Step 6: Commit**

```bash
git add app/graph/nodes.py tests/test_process_message.py
git commit -m "fix(nota-interna): divide mensagem no marcador em qualquer posicao, nota nao vaza"
```

---

## Self-review

Cobertura do spec: divisão no marcador em qualquer posição = Step 3; parte antes ao paciente, parte da nota privada = Step 3; fallback preservado = Step 3 + teste; `needs_price_notice` preservado na parte de paciente = Step 3; histórico completo gravado uma vez = Step 3. Testes cobrem meio, início, sem marcador e fallback.

Sem placeholders. Nomes de patch conferem com os imports em nodes.py (`get_conversation_id`, `add_private_note`).
