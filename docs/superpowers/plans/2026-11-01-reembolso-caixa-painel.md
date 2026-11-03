# Caixa de reembolso no painel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar o reembolso da taxa de reserva numa caixa negativa de R$100 no financeiro do paciente (painel), que a atendente fecha anexando o comprovante, lançando a saída na planilha Pagamentos e, opcionalmente, enviando a mensagem com o comprovante ao paciente.

**Architecture:** O painel (`dashboard/`) ganha a fonte da caixa, a baixa e a rota; o envio de mídia ao paciente vive em `app/` (Chatwoot + endpoint da Eva), chamado pelo dashboard como já faz com a confirmação de pagamento. Reusa a plumbing de planilha/Drive do `mark_paid` e o caminho de mensagem do `send_payment_confirmation`.

**Tech Stack:** Python 3.14, FastAPI, Supabase (postgrest), Google Sheets/Drive API, Chatwoot API, pytest. Dashboard é deployable separado e NÃO importa `app/`.

**Spec:** docs/superpowers/specs/2026-11-01-reembolso-caixa-painel-design.md

## Global Constraints

- Trabalhar numa git worktree própria (`.worktrees/<branch>`), nunca na `main` (CLAUDE.md).
- Valor do reembolso é sempre a taxa: **-100** (`BOOKING_FEE`), nunca valor de consulta.
- `dashboard/` não importa `app/`: lógica de planilha/Drive/Chatwoot usada pelo dashboard é espelhada dentro de `dashboard/`; envio de mídia ao paciente fica em `app/` e é chamado via HTTP (endpoint da Eva).
- Copy em pt-BR, tom humano, sem botões (ver [[templates-sem-botoes-tom-humano]]).
- Idempotência: baixa repetida não duplica linha na planilha nem reenvia mensagem.
- Testes de código que toca Supabase/Sheets/Drive/Chatwoot são mockados; seguir a estrutura de `dashboard/tests/` e `tests/`.
- Rodar `uv run pytest --tb=short` (raiz) e os testes do dashboard; tudo verde antes do PR.

## Review Focus

- **Primeira consulta de menor (duas linhas, uma taxa):** só a linha com `refund_requested_at` recebe a baixa; uma caixa só e uma linha de -100 só. Teste no Task 1 e Task 3.
- **Baixa repetida (idempotência):** `refund_completed_at` já preenchido não duplica planilha nem reenvia. Teste no Task 3.
- **Anexo fora da janela de 24h:** mídia não entregue → baixa registrada mesmo assim e painel avisa a atendente; nunca mandar "segue o comprovante" sem o arquivo ter ido. Teste no Task 6.
- **Destinatário = quem agendou/pagou (`contact_id`), não o paciente:** nome e telefone da mensagem saem do booker. Teste no Task 1.
- **Comprovante repetido:** reusar a trava de md5 (`_check_duplicate_receipt`) também na baixa de reembolso. Teste no Task 7.

---

### Task 1: Fonte da caixa de reembolso (`compute_reembolsos_pendentes`)

**Files:**
- Modify: `dashboard/payments.py`
- Test: `dashboard/tests/test_payments.py`

**Interfaces:**
- Consumes: o mesmo cliente supabase de `compute_pendencias`; a lógica de telefone do booker (`appointments.contact_id`) de `compute_pendencias` (extrair para helper `_booker_phone(patient, booker_id)` e reusar nos dois).
- Produces: `async compute_reembolsos_pendentes(client, patient_ids: list[str]) -> list[dict]`. Cada dict: `{"appointment_id": str, "patient_id": str, "paciente": str, "phone": str, "medico": str, "data_hora": str, "start_time": str, "tipo": "reembolso", "tipo_label": "Reembolso - Taxa de reserva", "valor": -100}`. Filtro: `status == "canceled"`, `refund_requested_at is not null`, `refund_completed_at is null`, `patient_id in patient_ids`. `patient_ids` vazio → `[]` sem consultar.

- [ ] **Step 1: Escrever os testes que falham**

```python
async def test_reembolso_pendente_vira_caixa_negativa(...):
    # appt canceled, refund_requested_at set, refund_completed_at null
    caixas = await compute_reembolsos_pendentes(client, [PID])
    assert len(caixas) == 1
    assert caixas[0]["tipo"] == "reembolso"
    assert caixas[0]["valor"] == -100
    assert caixas[0]["phone"] == BOOKER_PHONE  # contact_id, não o paciente

async def test_reembolso_ja_concluido_nao_aparece(...):
    # refund_completed_at preenchido → nenhuma caixa
    assert await compute_reembolsos_pendentes(client, [PID]) == []

async def test_primeira_consulta_menor_uma_caixa_so(...):
    # duas linhas, só uma com refund_requested_at → uma caixa
    assert len(await compute_reembolsos_pendentes(client, [PID])) == 1
```

- [ ] **Step 2: Rodar e ver falhar** — `cd dashboard && uv run pytest tests/test_payments.py -k reembolso -v` → FAIL (função não existe).
- [ ] **Step 3: Extrair `_booker_phone(patient: dict, booker_id) -> str` de `compute_pendencias` e implementar `compute_reembolsos_pendentes`** em `dashboard/payments.py`. Query em `appointments` filtrando status/refund; reusar o mesmo `select` de paciente/contatos de `compute_pendencias`.
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `feat(painel): fonte das caixas de reembolso pendente`.

---

### Task 2: Nome do arquivo de reembolso no Drive

**Files:**
- Modify: `dashboard/payments.py` (`build_receipt_filename`, `upload_comprovante`)
- Test: `dashboard/tests/test_payments.py`

**Interfaces:**
- Produces: `build_receipt_filename(patient_name, appointment_dt, amount, is_refund: bool = False)` — quando `is_refund`, acrescenta o sufixo `_Reembolso` ao nome. `upload_comprovante(paciente, data_hora, valor, content, mimetype, is_refund: bool = False)` repassa a flag. Mantém compatibilidade (default False não muda nada).

- [ ] **Step 1: Teste que falha**

```python
def test_filename_reembolso_tem_sufixo():
    name = build_receipt_filename("Tarso de Paula", "28/10/2026 15:00", "100", is_refund=True)
    assert name == "Tarso_de_Paula_28-10-2026_R$100_Reembolso"
```

- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Adicionar o parâmetro `is_refund` a `build_receipt_filename` e repassar em `upload_comprovante`.**
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `feat(painel): nome de comprovante de reembolso no Drive`.

---

### Task 3: Baixa do reembolso (`mark_refund_completed`)

**Files:**
- Modify: `dashboard/payments.py`
- Test: `dashboard/tests/test_payments.py`

**Interfaces:**
- Consumes: `_append_payment_sheet` (reuso: estica a Tabela e põe hyperlink quando há comprovante), `attendant_db.log_event`, o cliente supabase.
- Produces: `async mark_refund_completed(client, appointment_id: str, paciente: str, medico: str, data_hora: str, phone: str, drive_link: str = "", receipt_filename: str = "", file_md5: str = "") -> None`. Carimba `refund_completed_at` + `updated_at` na linha de `appointment_id` (a que tem `refund_requested_at`). Lança a linha de saída via `_append_payment_sheet(..., amount="-100", payment_type="Reembolso - Taxa de Reserva", drive_link=..., receipt_filename=...)`. Loga evento `refund_completed` `{"appointment_id", "amount": "100,00", "registered_via": "dashboard", **(file_md5 se houver)}`. Espelha em `messages` quando há comprovante, no formato `"[imagem]: COMPROVANTE DE REEMBOLSO: R$ 100,00 — registrado pela atendente [drive_link:...]"`. Idempotente: se `refund_completed_at` já estiver preenchido, não faz nada.

- [ ] **Step 1: Testes que falham**

```python
async def test_mark_refund_carimba_e_lanca_saida(monkeypatch, ...):
    appends = []
    monkeypatch.setattr(payments, "_append_payment_sheet", fake(appends))
    await mark_refund_completed(client, AID, "Tarso", "Dra. Bruna", "28/10/2026 15:00", PHONE, drive_link="d", receipt_filename="f")
    assert appt_row(AID)["refund_completed_at"] is not None
    assert appends[0]["payment_type"] == "Reembolso - Taxa de Reserva"
    assert appends[0]["amount"] == "-100"
    assert any(e["event_type"] == "refund_completed" for e in events)

async def test_mark_refund_idempotente(...):
    # refund_completed_at já preenchido → não chama _append_payment_sheet
    assert appends == []
```

- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar `mark_refund_completed`** espelhando a estrutura best-effort de `mark_paid` (try/except em planilha/evento/espelho).
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `feat(painel): baixa do reembolso grava banco, planilha e evento`.

---

### Task 4: Enviar mídia ao paciente pelo Chatwoot (`send_message_with_attachment`)

**Files:**
- Modify: `app/chatwoot.py`
- Test: `tests/test_chatwoot.py` (criar se não existir)

**Interfaces:**
- Produces: `async send_message_with_attachment(conversation_id: int, text: str, file_bytes: bytes, filename: str, content_type: str) -> None`. POST multipart em `/api/v1/accounts/{account}/conversations/{id}/messages` com `content`, `message_type=outgoing`, `private=false` e `attachments[]` (o arquivo). Usa `_bot_headers()` e `_request`/httpx como as demais.

- [ ] **Step 1: Teste que falha** — mocka httpx e verifica que o POST vai como multipart com o arquivo em `attachments[]` e `content` = texto.
- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar `send_message_with_attachment`** (httpx `files=` multipart; não usar `json=`).
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `feat(chatwoot): envio de mensagem com anexo ao paciente`.

---

### Task 5: Endpoint da Eva para a mensagem de reembolso (`send_refund_message`)

**Files:**
- Modify: `app/panel_booking.py`, `app/main.py` (registrar a rota)
- Test: `tests/test_panel_booking.py`

**Interfaces:**
- Consumes: `_window_open(phone)`, `send_text`, `send_message_with_attachment` (Task 4), `find_or_create_conversation`, `save_message`.
- Produces: `async send_refund_message(body: dict) -> tuple[int, dict]`. `body`: `{"phone", "text", "media": {"b64", "filename", "content_type"} | None}`. Regra: dentro da janela de 24h → se `media` → `send_message_with_attachment`; senão `send_text`. Fora da janela → NÃO envia (não há template de reembolso aprovado) e devolve `{"sent": False, "motivo": "janela_fechada"}`. Com envio: espelha em `messages`/checkpoint como `send_payment_confirmation`. Rota: `@app.post("/admin/panel/refund-message")` chamando `_run_panel(panel_booking.send_refund_message, request)`, ao lado de `payment-confirmation` (app/main.py:636).

- [ ] **Step 1: Testes que falham**

```python
async def test_refund_message_dentro_janela_com_anexo(monkeypatch):
    # _window_open True, media presente → chama send_message_with_attachment, sent True
async def test_refund_message_dentro_janela_sem_anexo(monkeypatch):
    # _window_open True, media None → chama send_text, sent True
async def test_refund_message_fora_janela_nao_envia(monkeypatch):
    # _window_open False → sent False, motivo janela_fechada, nada enviado
```

- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar `send_refund_message` e registrar a rota em `app/main.py`.**
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `feat(eva): endpoint de mensagem de reembolso (texto ou anexo)`.

---

### Task 6: Rota de baixa de reembolso no dashboard + cliente da mensagem

**Files:**
- Modify: `dashboard/attendant_routes.py`
- Test: `dashboard/tests/test_attendant_routes.py`

**Interfaces:**
- Consumes: `payments.compute_reembolsos_pendentes` (Task 1), `payments.upload_comprovante(..., is_refund=True)` (Task 2), `payments.mark_refund_completed` (Task 3), `_check_duplicate_receipt`, `eva_client.post("/admin/panel/refund-message", ...)`, `_confirmation_names`, `_first_name`.
- Produces:
  - Estende `GET /pagamentos` para devolver também as caixas de reembolso: retorna `await compute_pendencias(...) + await compute_reembolsos_pendentes(...)`.
  - `refund_message(contact_first: str, has_comprovante: bool) -> str` (helper):
    - com comprovante: `"Olá, {contact_first}! 💜 Segue o comprovante do seu reembolso da taxa de reserva. Quando quiser agendar a próxima consulta, estamos à disposição!"`
    - sem comprovante: `"Olá, {contact_first}! 💜 Seu reembolso da taxa de reserva foi realizado. Quando quiser agendar a próxima consulta, estamos à disposição!"`
  - `POST /pagamentos/{appointment_id}/reembolso` (multipart Form): `paciente, medico, data_hora, phone, enviar_mensagem: bool, conversation_id: int|None, file: UploadFile|None, confirmar_duplicado: bool`. Fluxo: `_assert_appointment_scope`; se `file` → md5 + `_check_duplicate_receipt` + `upload_comprovante(..., is_refund=True)`; `mark_refund_completed(...)`; se `enviar_mensagem` → monta `refund_message` (contact_first via `_confirmation_names`), chama `/admin/panel/refund-message` com `media` (b64 do arquivo) quando houver, e devolve `confirmacao` ∈ {`enviada`,`fora_da_janela`,`falhou`}. Retorno `{"ok": True, "confirmacao": <str|"">}` (`""` quando `enviar_mensagem` False).

- [ ] **Step 1: Testes que falham**

```python
async def test_reembolso_sem_mensagem_so_registra(monkeypatch):
    # enviar_mensagem False → mark_refund_completed chamado, refund-message NÃO chamado, confirmacao ""
async def test_reembolso_com_comprovante_e_mensagem(monkeypatch):
    # file presente + enviar_mensagem True → upload is_refund=True, texto cita comprovante, media enviada
async def test_reembolso_sem_comprovante_texto_adaptado(monkeypatch):
    # sem file + enviar_mensagem True → texto NÃO cita comprovante
async def test_reembolso_comprovante_repetido_bloqueia(monkeypatch):
    # md5 repetido sem confirmar_duplicado → 409, nada gravado
```

- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar o helper, a rota e a extensão do GET.**
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit** — `feat(painel): rota de baixa de reembolso com mensagem opcional`.

---

### Task 7: Caixa de reembolso no front (`atendente.html`)

**Files:**
- Modify: `dashboard/templates/atendente.html`

**Interfaces:**
- Consumes: o array de `GET /api/atendente/pagamentos` agora traz itens `tipo: "reembolso"`; a rota `POST .../reembolso`.

- [ ] **Step 1: Estender `pagamentoCard(p)`** para `p.tipo === "reembolso"`: card com realce negativo, rótulo "Reembolso - Taxa de reserva", valor "-R$ 100,00", um checkbox "Avisar o paciente" (marcado por padrão), "Anexar comprovante" (opcional, mesmo input de arquivo) e botão "Registrar devolução".
- [ ] **Step 2: Handler do botão** → `POST /api/atendente/pagamentos/{appt}/reembolso` (multipart com `enviar_mensagem`, `file` opcional, `phone`, etc.), e feedback pelo `confirmacao`: `enviada` → ok; `fora_da_janela`/`falhou` → avisar "a mensagem não saiu, envie o comprovante ao paciente na mão".
- [ ] **Step 3: Verificação manual** — abrir o painel embutido, cancelar uma consulta de teste com devolver, ver a caixa aparecer, dar baixa com e sem comprovante, conferir planilha e conversa. (Sem teste automatizado de JS; seguir o padrão do projeto.)
- [ ] **Step 4: Commit** — `feat(painel): caixa de reembolso no financeiro do paciente`.

---

### Task 8: Verificação de ponta a ponta e regressão

**Files:**
- Test: suíte inteira

- [ ] **Step 1: Rodar `uv run pytest --tb=short` na raiz e a suíte do dashboard;** tudo verde.
- [ ] **Step 2: Conferir que os caminhos existentes de cancelamento/reembolso (Eva `register_refund_request`/`confirm_refund_completed`) seguem passando** — nenhuma dupla mensagem nem dupla linha.
- [ ] **Step 3: Commit** (se houver ajuste) e abrir o PR a partir da worktree.
