# Templates para avisos fora da janela de 24h — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar por template aprovado da Meta os quatro avisos proativos ao paciente que hoje somem em silêncio quando a janela de 24h está fechada: agendamento pelo painel (com taxa / confirmada), reembolso da taxa, e liberação de vaga por remarcação não confirmada.

**Architecture:** Espelhar o padrão já canônico ("texto livre dentro da janela, template aprovado fora") de `send_payment_confirmation` e `scripts/send_payment_reminders`. Nada de novo na Meta (os 4 templates já existem). Cada caso passa a montar `body_params` posicionais e, fora da janela, chama `app/chatwoot.send_template_message` em vez de cair em `not_delivered`/`sent=False`.

**Tech Stack:** Python, FastAPI, LangGraph, Supabase, Chatwoot (Meta WhatsApp). Testes com `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-11-20-templates-fora-da-janela-design.md`

## Global Constraints

- Feature nasce em worktree (`.worktrees/<branch>`), PR de lá; nunca commitar na `main` (CLAUDE.md).
- Rodar `uv run pytest --tb=short`; CI verde antes do merge.
- Todo envio via Chatwoot/Meta é mockado nos testes (nunca chama a rede real).
- Templates: categoria `UTILITY`, idioma `pt_BR`. `body_params` é dict posicional `{"1": ..., "2": ...}`, cada valor uma string de uma linha só, sem `\n`, `\t` nem espaços duplicados (a Meta rejeita).
- Ordem das variáveis é a confirmada pela Ayexa (ver spec), fixa por template:
  - `consulta_agendada_taxa` e `consulta_agendada_confirmada`: `{{1}}` nome de quem recebe · `{{2}}` paciente · `{{3}}` médico · `{{4}}` data e hora.
  - `reembolso_taxa_reserva`: `{{1}}` nome · `{{2}}` valor · `{{3}}` paciente.
  - `vaga_liberada_remarcacao`: `{{1}}` nome · `{{2}}` paciente · `{{3}}` médico.
- Dentro da janela o comportamento atual (texto livre) não muda. Template só fora da janela.
- `consulta_agendada_taxa` usa prazo de 2 horas fixas; o `payment_deadline_at` ajustável do painel não é refletido no template.
- Agendamento dividido (duas linhas / `pending_part2`) fora da janela continua `not_delivered` (o template de slot único não o representa). Template cobre só o slot único.

## Review Focus

- **Agendamento com duas linhas (1ª consulta infantil dividida) fora da janela:** o template de slot único não deve ser disparado; o recipiente continua em `not_delivered`. Coberto por teste na Task 2.
- **`contact_first_name` vazio no `{{1}}`:** contato sem nome não pode gerar `body_param` vazio nem com espaço. Usar o mesmo fallback do texto livre (`"tudo bem"`). Coberto na Task 2.
- **Reembolso fora da janela COM comprovante anexado:** o anexo não é entregue; manda o template texto mesmo assim e sinaliza para a atendente que o anexo não foi (retorno `anexo_entregue=False`). Coberto na Task 3.
- **Remarcação: vaga só é liberada se o aviso saiu.** Fora da janela, só liberar a vaga quando o template foi entregue; falha no envio = não libera (igual à regra atual com texto). Coberto na Task 4.
- **Valor do reembolso formatado:** `{{2}}` precisa sair "R$ 100,00" (vírgula decimal, ponto de milhar), nunca `100`. Coberto na Task 3.

---

## Task 1: Helper do "quando" em `app/booking_texts.py`

Extrai de `format_appt_line` a string de data/hora sem o prefixo do médico, para virar o `{{4}}` do template de agendamento.

**Files:**
- Modify: `app/booking_texts.py`
- Test: `tests/test_booking_texts.py`

**Interfaces:**
- Produces: `appt_when(start: datetime) -> str` → `"quinta-feira, 12/11/2026 às 09:00"` (dia da semana + `%d/%m/%Y às %H:%M`, sem médico, sem nota). `start` já em America/Recife.
- `format_appt_line` continua com a mesma assinatura e saída de hoje, agora reusando `appt_when`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_booking_texts.py
from datetime import datetime
from app.booking_texts import appt_when, format_appt_line

def test_appt_when_weekday_and_datetime_no_doctor():
    start = datetime(2026, 11, 12, 9, 0)  # quinta
    assert appt_when(start) == "quinta-feira, 12/11/2026 às 09:00"

def test_format_appt_line_still_prefixes_doctor_and_note():
    start = datetime(2026, 11, 12, 9, 0)
    assert format_appt_line("julio", start, "Domiciliar") == \
        "Dr. Júlio — quinta-feira, 12/11/2026 às 09:00 (Domiciliar)"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_booking_texts.py -q`
Expected: FAIL (`appt_when` não existe).

- [ ] **Step 3: Implementar `appt_when(start: datetime) -> str` em `app/booking_texts.py`**

Mover o cálculo `f"{_WEEKDAYS[start.weekday()]}, {start.strftime('%d/%m/%Y às %H:%M')}"` para `appt_when`; `format_appt_line` chama `appt_when(start)` e mantém o prefixo `label + " — "` e o sufixo da nota.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_booking_texts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/booking_texts.py tests/test_booking_texts.py
git commit -m "feat(booking): extrai appt_when para o {{4}} dos templates"
```

---

## Task 2: Template fallback no agendamento pelo painel

`deliver_message` passa a aceitar um construtor de template por destinatário; fora da janela, slot único vira template em vez de `not_delivered`.

**Files:**
- Modify: `app/panel_booking.py` (`deliver_message`, `send_booking_message`, `handle`; novo mapa de templates)
- Test: `tests/test_panel_booking.py`

**Interfaces:**
- Consumes: `app.booking_texts.appt_when` (Task 1); `app.chatwoot.find_or_create_conversation`, `send_template_message`; `DOCTOR_LABELS`.
- Produces:
  - `_booking_template(kind: str) -> str`: `"normal" → "consulta_agendada_taxa"`; `"taxa_paga" | "taxa_isenta" | "cortesia" → "consulta_agendada_confirmada"`.
  - `deliver_message(patient_id, contact_id, text_for, patient_name, doctor, template_for=None) -> dict`. `template_for: Callable[[dict], tuple[str, dict[str,str]] | None] | None`. Para cada recipiente com janela fechada: se `template_for` existe e `template_for(c)` não é None, envia o template (`content = text_for(c)`), grava `messages`+checkpoint igual ao ramo de texto, e entra em `sent`; senão, `not_delivered` (comportamento de hoje). Janela aberta: inalterado.
  - `send_booking_message(patient_id, contact_id, kind, lines, pending_part2, patient_name, doctor, when=None) -> dict`. Monta `template_for` só quando `when is not None and len(lines) == 1 and not pending_part2`; params `{"1": contact_first_name(c) or "tudo bem", "2": patient_name, "3": DOCTOR_LABELS.get(doctor, doctor), "4": when}`.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_panel_booking.py — mesma montagem dos testes de envio existentes
# (patch de _recipients, _window_open, send_text, send_template_message, graph, save_message)

@pytest.mark.asyncio
async def test_booking_closed_window_single_slot_sends_template():
    # 1 recipiente, _window_open=False, kind="normal", 1 linha, pending_part2=False
    # espera: send_template_message chamado com "consulta_agendada_taxa", pt_BR, UTILITY,
    #   body_params {"1":"Carla","2":"Nina","3":"Dr. Júlio","4":"quinta-feira, 12/11/2026 às 09:00"}
    #   e content == texto livre; out["sent"] == ["Carla"], not_delivered == []
    ...

@pytest.mark.asyncio
async def test_booking_closed_window_confirmada_template_for_paid_kind():
    # kind="taxa_paga" -> template "consulta_agendada_confirmada"
    ...

@pytest.mark.asyncio
async def test_booking_closed_window_split_stays_not_delivered():
    # when=None (ou len(lines)>1 / pending_part2=True) -> NÃO chama send_template_message;
    # out["not_delivered"] == ["Carla"]
    ...

@pytest.mark.asyncio
async def test_booking_closed_window_empty_contact_name_uses_fallback():
    # contato sem nome -> body_params["1"] == "tudo bem" (nunca "" nem " ")
    ...
```

Atualizar o teste existente `test_send_skips_closed_window_and_writes_checkpoint_when_open` (e vizinhos que esperam `not_delivered` com janela fechada): eles chamam `deliver_message`/`send_booking_message` sem `template_for`/`when`, então o fallback continua `not_delivered` — confirmar que seguem válidos; onde um teste passar por `send_booking_message` com janela fechada esperando `not_delivered`, garantir que passa `when=None` (default) para manter o comportamento.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: FAIL (novos testes; `template_for`/`when`/`_booking_template` não existem).

- [ ] **Step 3: Implementar em `app/panel_booking.py`**

Adicionar o mapa `_BOOKING_TEMPLATE_BY_KIND` + `_booking_template(kind)`. Em `deliver_message`, no ramo de janela fechada (`not phone or not await _window_open(phone)`), antes de `not_delivered.append(name)`: se `phone` e `template_for` e `(spec := template_for(c)) is not None`, montar `tmpl, params = spec`, `text = text_for(c)`, `thread = _thread(phone)`, `conversation_id = await find_or_create_conversation(thread)`, `await send_template_message(conversation_id, tmpl, "pt_BR", "UTILITY", {str(k): str(v) for k,v in params.items()}, text)`, depois o mesmo bloco de `save_message` + checkpoint do ramo de texto, e `sent.append(name)`; em exceção, `not_delivered.append(name)`. `send_booking_message` monta `template_for` como no bloco Interfaces e repassa. `handle` calcula `when`: `appts = created["appointments"]; when = appt_when(datetime.fromisoformat(appts[0]["start"])) if len(appts) == 1 and not created["pending_part2"] else None`, e passa `when` para `send_booking_message`. Importar `appt_when` de `app.booking_texts`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_panel_booking.py -q`
Expected: PASS (novos + os atualizados).

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py tests/test_panel_booking.py
git commit -m "feat(painel): agendamento fora da janela entrega por template"
```

---

## Task 3: Template fallback no reembolso da taxa

Fora da janela, o aviso de reembolso vira template `reembolso_taxa_reserva`; o valor passa a vir do painel (campo editável, padrão R$ 100,00).

**Files:**
- Modify: `app/panel_booking.py` (`send_refund_message`)
- Modify: `dashboard/attendant_routes.py` (builder do template, `_send_refund_message`, endpoint `reembolso`)
- Modify: `dashboard/templates/atendente.html` (campo de valor no card de reembolso)
- Test: `tests/test_panel_booking.py`, `tests/test_attendant_routes.py` (ou o arquivo onde o reembolso já é testado — confirmar; criar nada novo se já existir)

**Interfaces:**
- Consumes: `send_template_message`, `find_or_create_conversation`; `_format_brl` e `_first_name` de `dashboard/attendant_routes.py`.
- Produces:
  - `send_refund_message(body)` aceita `body["template"]` (str, opcional) e `body["params"]` (dict, opcional). Janela aberta: inalterado (texto, ou anexo quando há `media`). Janela fechada: se `template` → `send_template_message(conv, template, "pt_BR", "UTILITY", params, text)` + `save_message`/checkpoint; retorna `{"sent": True, "via": "template", "anexo_entregue": <False se havia media, senão True>}`. Sem `template` e fora da janela: retorna `{"sent": False, "motivo": "janela_fechada"}` (back-compat).
  - `refund_confirmation(contact_first: str, valor: int, patient_first: str) -> tuple[str, dict[str,str], str]` em `attendant_routes.py`: retorna `("reembolso_taxa_reserva", {"1": contact_first, "2": _format_brl(valor), "3": patient_first}, text)`, com `text = refund_message(contact_first, has_comprovante=False)`.
  - `_send_refund_message(phone, text, media, template="", params=None) -> str` repassa `template`/`params` no POST para `/admin/panel/refund-message`; retorna `"enviada"` quando `payload.get("sent")`, senão `"fora_da_janela"`/`"falhou"` (igual a hoje).
  - Endpoint `reembolso`: novo `valor: int = Form(default=100)`; monta `contact_first, _pf = await _confirmation_names(phone, paciente)`, `patient_first = _first_name(paciente)`, `template, params, text = refund_confirmation(contact_first, valor, patient_first)`, e chama `_send_refund_message(phone, text, media, template, params)`.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_panel_booking.py
@pytest.mark.asyncio
async def test_refund_closed_window_with_template_sends_template_text():
    body = {"phone": "...", "text": "Seu reembolso...", "template": "reembolso_taxa_reserva",
            "params": {"1": "Carla", "2": "R$ 100,00", "3": "Nina"}}
    # _window_open=False -> send_template_message chamado; retorno sent True, via "template",
    # anexo_entregue True (sem media)

@pytest.mark.asyncio
async def test_refund_closed_window_with_media_sends_template_and_flags_anexo():
    # body com media + template, _window_open=False -> send_template_message chamado
    # (NÃO send_message_with_attachment); retorno anexo_entregue == False

@pytest.mark.asyncio
async def test_refund_closed_window_no_template_still_janela_fechada():
    # body sem template, _window_open=False -> sent False, motivo "janela_fechada"
```

```python
# teste do builder (arquivo de teste do attendant_routes / dashboard)
def test_refund_confirmation_params_and_template():
    tmpl, params, text = refund_confirmation("Carla", 100, "Nina")
    assert tmpl == "reembolso_taxa_reserva"
    assert params == {"1": "Carla", "2": "R$ 100,00", "3": "Nina"}
    assert "Carla" in text
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_panel_booking.py -q` e o teste do builder.
Expected: FAIL.

- [ ] **Step 3: Implementar**

`app/panel_booking.py::send_refund_message`: ler `template`/`params`; no ramo `not await _window_open(phone)`, se `template`: `conv = await find_or_create_conversation(thread)`, `await send_template_message(conv, template, "pt_BR", "UTILITY", {str(k):str(v) for k,v in (params or {}).items()}, text)`, `save_message`+checkpoint, retornar `{"sent": True, "via": "template", "anexo_entregue": media is None}`; senão retornar `{"sent": False, "motivo": "janela_fechada"}`. `dashboard/attendant_routes.py`: adicionar `refund_confirmation`, estender `_send_refund_message` e o endpoint `reembolso` (campo `valor`, montar template/params, passar adiante). `dashboard/templates/atendente.html`: no card de reembolso (perto de `.avisar-input`, ~linha 1190) adicionar um `<input type="number" class="valor-reembolso-input" value="100" min="0">` e, em `registrarReembolso`, `form.append("valor", String(valor))` validando inteiro ≥ 0. Quando `res.anexo_entregue === false`, ajustar o alert para avisar que o anexo não foi entregue (a atendente envia o comprovante na mão).

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_panel_booking.py -q` e o teste do builder.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/panel_booking.py dashboard/attendant_routes.py dashboard/templates/atendente.html tests/
git commit -m "feat(reembolso): template fora da janela com valor editável no painel"
```

---

## Task 4: Template fallback na liberação de vaga por remarcação

O cron `release_pending_reschedules` passa a mandar template `vaga_liberada_remarcacao` fora da janela e só libera a vaga quando o aviso saiu.

**Files:**
- Modify: `scripts/release_pending_reschedules.py`
- Test: `tests/test_release_pending_reschedules.py`

**Interfaces:**
- Consumes: `scripts.send_payment_reminders._window_open` (assinatura `(client, phone, now)`) e `._send_template` (assinatura `(phone, template_name, body_params, content)`), importados para não duplicar a lógica de janela.
- Produces: no loop, substituir o envio direto por: montar `body_params = {"1": contact_first, "2": patient_first or contact_first, "3": doctor_label}`; se `await _window_open(client, phone, now)` → `await send_whatsapp(phone, msg)`; senão → `await _send_template(phone, "vaga_liberada_remarcacao", body_params, msg)`. `whatsapp_ok = True` só em sucesso; em exceção, `whatsapp_ok=False` e a vaga NÃO é liberada (igual a hoje).

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_release_pending_reschedules.py — estilo dos testes existentes (patch em rel)
def test_release_closed_window_sends_template_and_releases():
    # _window_open -> False; espera _send_template chamado com "vaga_liberada_remarcacao"
    # e body_params {"1": contact_first, "2": patient ou contato, "3": doctor_label};
    # status vira pending_reschedule (vaga liberada)

def test_release_open_window_sends_free_text():
    # _window_open -> True; espera send_whatsapp chamado, _send_template NÃO chamado

def test_release_template_failure_does_not_release_slot():
    # _send_template levanta -> vaga NÃO liberada (update de status não chamado)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_release_pending_reschedules.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementar em `scripts/release_pending_reschedules.py`**

Importar `_window_open` e `_send_template` de `scripts.send_payment_reminders`. Trocar o bloco "1. Notifica paciente": montar `body_params`, escolher texto/template pela janela, `whatsapp_ok` conforme sucesso. Manter a guarda `manual_hold` e o `if not whatsapp_ok: continue` antes de liberar o slot.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_release_pending_reschedules.py -q`
Expected: PASS.

- [ ] **Step 5: Rodar a suíte inteira e commitar**

Run: `uv run pytest --tb=short`
Expected: PASS (suíte verde).

```bash
git add scripts/release_pending_reschedules.py tests/test_release_pending_reschedules.py
git commit -m "feat(remarcacao): libera vaga via template fora da janela"
```

---

## Notas de verificação final

- `uv run pytest --tb=short` verde, incluindo os novos testes de template dos 4 casos.
- Teste manual sugerido (staging/replay): marcar pelo painel um contato com janela fechada e conferir que o template chega; repetir para reembolso (com e sem comprovante) e para o cron de remarcação.
- Fora de escopo (follow-up em PR próprio): aviso ao terceiro pagante em `app/graph/tools.py::register_payment` (reaproveita os templates `pagamento_*` existentes).
