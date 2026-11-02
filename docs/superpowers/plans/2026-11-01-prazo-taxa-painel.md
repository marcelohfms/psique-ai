# Prazo da taxa de reserva ajustável pelo painel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar à atendente um botão no painel para adiar a data limite de pagamento da taxa de reserva de uma consulta, sem avisar o paciente.

**Architecture:** Reusa a coluna `appointments.payment_deadline_at` (o cron `scripts/send_payment_reminders.py` já respeita). Uma função nova em `dashboard/payments.py` valida e grava a data; uma rota nova em `dashboard/attendant_routes.py` a expõe; um botão em `dashboard/templates/atendente.html` chama a rota. De passagem, o helper de escopo passa a tratar o `appointment_id` agrupado (vírgula), o que também conserta a rota `isentar`.

**Tech Stack:** FastAPI, Supabase (async client), pytest, HTML/JS puro no template.

**Spec:** `docs/superpowers/specs/2026-11-01-prazo-taxa-painel-design.md`

## Global Constraints

- Nada de mensagem ao paciente nesta feature; ajuste só no banco.
- Não mexer no cron `scripts/send_payment_reminders.py` nem na ferramenta `extend_payment_deadline` da Eva.
- Fuso: `America/Recife` (`_TZ` já existe em `dashboard/payments.py`).
- O prazo gravado é `payment_deadline_at`; ao gravar, zerar também `payment_reminder_sent_at`.
- `appointment_id` pode vir como vários ids separados por vírgula (1ª consulta dividida) — tratar como `mark_fee_waived`/`mark_paid` já fazem (`.split(",")`).
- Testes do painel rodam de dentro de `dashboard/` (`cd dashboard && .venv/bin/pytest` ou o runner já usado na pasta).

## Review Focus

- **Data no passado** → a rota deve recusar com 400, não gravar um prazo já vencido.
- **Data cujo fim do dia cai depois do início da consulta** → recusar com 400 (viola "antes da consulta"). Coberto na Task 2.
- **Corpo sem `data_limite` ou com formato inválido** → 400 claro, sem estourar 500. Coberto na Task 3.
- **Id agrupado (1ª consulta dividida)** → escopo valida cada id e o prazo usa o `start_time` mais cedo. Coberto nas Tasks 1 e 2.
- **Consulta hoje** → nenhum fim-de-dia futuro cabe antes dela, então 400 (esperado). Coberto na Task 2.

---

### Task 1: Escopo trata `appointment_id` agrupado (conserta o `isentar`)

**Files:**
- Modify: `dashboard/attendant_routes.py` (`_assert_appointment_scope`, ~linha 54)
- Test: `dashboard/tests/test_attendant_routes.py`

**Interfaces:**
- Produces: `_assert_appointment_scope(phone: str, appointment_id: str)` passa a aceitar `"id1,id2"`: separa por vírgula e exige que **cada** id esteja no escopo; 403 se qualquer um faltar.

- [ ] **Step 1: Escrever o teste que falha** em `dashboard/tests/test_attendant_routes.py`: `test_isentar_aceita_appointment_agrupado`. Sem o fixture `_bypass_scope`. Mocka `attendant_db.scope_for_phone` devolvendo `("c1", {"p1"})`, `attendant_db.get_appointment_patient_id` devolvendo `"p1"` para `"a1"` e `"a2"`, e `payments.mark_fee_waived`/`_confirmation_names`/`_send_via_eva`/`attendant_db.log_event` como no-ops. `POST /api/atendente/pagamentos/a1,a2/isentar?token=...` com body de isenção → espera 200.
- [ ] **Step 2: Rodar e ver falhar** — `cd dashboard && .venv/bin/pytest tests/test_attendant_routes.py::test_isentar_aceita_appointment_agrupado -v`. Esperado: FAIL (403, porque hoje o `.eq` exato não acha `"a1,a2"`).
- [ ] **Step 3: Implementar** — em `_assert_appointment_scope`, iterar `appointment_id.split(",")`, chamar `get_appointment_patient_id` para cada id e lançar 403 se algum `pid` for None ou fora de `patient_ids`.
- [ ] **Step 4: Rodar e ver passar** — mesmo comando. Esperado: PASS.
- [ ] **Step 5: Commit** — `git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py && git commit -m "fix(painel): valida escopo de appointment agrupado (conserta isentar na 1ª consulta dividida)"`

---

### Task 2: `payments.set_payment_deadline` grava e valida o prazo

**Files:**
- Modify: `dashboard/payments.py` (função nova, perto de `mark_fee_waived`)
- Test: `dashboard/tests/test_payments.py`

**Interfaces:**
- Produces: `async def set_payment_deadline(client, appointment_id: str, deadline_date: datetime.date) -> str`. Busca o `start_time` de cada id (`.split(",")`) e usa o mais cedo. Calcula `deadline = datetime(deadline_date, 23:59:59, tz=_TZ)`. Valida: `deadline > agora` e `deadline < start_time_mais_cedo`; se não, `raise ValueError(msg)` com mensagem em pt-BR. Se válido, para cada id faz `.update({"payment_deadline_at": deadline.isoformat(), "payment_reminder_sent_at": None})`. Retorna `deadline.isoformat()`.

- [ ] **Step 1: Escrever os testes que falham** em `dashboard/tests/test_payments.py`, com um cliente Supabase mockado (espelhar os mocks já usados em `test_payments.py`):
  - `test_set_payment_deadline_grava_fim_do_dia_e_zera_lembrete`: consulta com `start_time` em +10 dias; `deadline_date` = daqui a 3 dias → o `.update` recebe `payment_deadline_at` às 23:59:59 do dia escolhido e `payment_reminder_sent_at: None`.
  - `test_set_payment_deadline_permite_hoje_para_consulta_futura`: `start_time` em +5 dias, `deadline_date` = hoje → grava sem erro.
  - `test_set_payment_deadline_recusa_data_passada`: `deadline_date` = ontem → `ValueError`.
  - `test_set_payment_deadline_recusa_data_depois_da_consulta`: `start_time` amanhã 10:00, `deadline_date` = amanhã → `ValueError` (23:59 de amanhã é depois das 10:00).
  - `test_set_payment_deadline_agrupado_usa_start_time_mais_cedo`: ids `"a1,a2"` com starts diferentes; valida contra o mais cedo e dá `.update` nos dois.
- [ ] **Step 2: Rodar e ver falhar** — `cd dashboard && .venv/bin/pytest tests/test_payments.py -k set_payment_deadline -v`. Esperado: FAIL (função não existe).
- [ ] **Step 3: Implementar** `set_payment_deadline` em `dashboard/payments.py` conforme o bloco Interfaces. Usar `_TZ` e `datetime.now(_TZ)`.
- [ ] **Step 4: Rodar e ver passar** — mesmo comando. Esperado: PASS.
- [ ] **Step 5: Commit** — `git add dashboard/payments.py dashboard/tests/test_payments.py && git commit -m "feat(painel): set_payment_deadline grava prazo da taxa (fim do dia, antes da consulta)"`

---

### Task 3: Rota `POST /pagamentos/{appointment_id}/prazo`

**Files:**
- Modify: `dashboard/attendant_routes.py` (body model + rota, perto de `isentar`)
- Test: `dashboard/tests/test_attendant_routes.py`

**Interfaces:**
- Consumes: `_assert_appointment_scope` (Task 1), `payments.set_payment_deadline` (Task 2), `attendant_db.log_event`.
- Produces: body `AtendentePrazoBody(phone: str, data_limite: str)` (`data_limite` = `"YYYY-MM-DD"`). Rota: valida token, valida escopo, faz `date.fromisoformat(body.data_limite)` (400 se inválido), chama `set_payment_deadline` (ValueError → 400 com a mensagem), registra `log_event("attendant_payment_deadline_set", phone, {"appointment_id", "data_limite"})`, retorna `{"ok": True, "deadline": <iso>}`.

- [ ] **Step 1: Escrever os testes que falham** em `dashboard/tests/test_attendant_routes.py` (usar o fixture `_bypass_scope`):
  - `test_prazo_ok`: mocka `payments.set_payment_deadline` → iso; `attendant_db.log_event` no-op. `POST /api/atendente/pagamentos/a1/prazo?token=...` body `{"phone": "...", "data_limite": "2026-11-20"}` → 200, `ok=True`, e `set_payment_deadline` chamado com `date(2026,11,20)`.
  - `test_prazo_data_invalida_400`: `data_limite: "20/11"` → 400, sem chamar `set_payment_deadline`.
  - `test_prazo_valueerror_vira_400`: `set_payment_deadline` levanta `ValueError("...")` → 400 com a mensagem.
- [ ] **Step 2: Rodar e ver falhar** — `cd dashboard && .venv/bin/pytest tests/test_attendant_routes.py -k prazo -v`. Esperado: FAIL (rota não existe).
- [ ] **Step 3: Implementar** o body model e a rota conforme Interfaces.
- [ ] **Step 4: Rodar e ver passar** — mesmo comando. Esperado: PASS.
- [ ] **Step 5: Commit** — `git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py && git commit -m "feat(painel): rota para ajustar prazo da taxa de reserva"`

---

### Task 4: Botão "Ajustar prazo" no card de taxa

**Files:**
- Modify: `dashboard/templates/atendente.html` (`pagamentoCard` ~1049 e um `ajustarPrazoTaxa` novo perto de `isentarTaxa` ~1160)

**Interfaces:**
- Consumes: a rota da Task 3; os `data-*` do card (`data-appt`, `data-paciente`, `data-phone`).

- [ ] **Step 1: Implementar o botão e o handler.** No `pagamentoCard`, só quando `p.tipo === "taxa"`, acrescentar ao rodapé um `<button onclick="ajustarPrazoTaxa(this)" class="btn">Ajustar prazo</button>`. Criar `ajustarPrazoTaxa(btn)`: lê o card, mostra um `<input type="date">` (mínimo = hoje) numa linha inline com um botão "Salvar" e uma linha de ajuda: "O paciente tem até o fim deste dia para pagar a taxa. Antes disso, a consulta não é cancelada por falta de pagamento." Ao salvar, `POST /api/atendente/pagamentos/${card.dataset.appt}/prazo?token=...` com `{phone: card.dataset.phone, data_limite: <valor do input>}`. Em erro, `alert` com o `detail` retornado; em sucesso, `alert("Prazo ajustado ✅")` e fecha a linha (sem remover o card — a pendência continua até pagar).
- [ ] **Step 2: Verificação manual.** `cd dashboard && .venv/bin/python -c "import main"` (ou o import check já usado na pasta) para garantir que o template não quebrou nada no carregamento. Conferir à vista que o botão aparece só no card de taxa e que a data mínima é hoje.
- [ ] **Step 3: Commit** — `git add dashboard/templates/atendente.html && git commit -m "feat(painel): botão Ajustar prazo no card de taxa de reserva"`

---

### Task 5: Suíte verde

- [ ] **Step 1: Rodar a suíte do painel** — `cd dashboard && .venv/bin/pytest -q`. Esperado: tudo PASS.
- [ ] **Step 2: Rodar a suíte principal** (nada deve ter mudado nela, mas confere) — `uv run pytest --tb=short`. Esperado: PASS.
