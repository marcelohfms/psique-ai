# Comprovantes complementares e valor rígido — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a Eva somar PIX complementares ao total já pago de uma consulta e quitá-la quando o acumulado alcança o valor rígido da tabela de preço, salvo pacientes com valor especial.

**Architecture:** Uma coluna `amount_paid_cents` em `appointments` guarda o total recebido em centavos. Uma função SQL incrementa essa coluna de forma atômica. O `register_payment` da Eva e o `mark_paid` do painel passam a alimentar essa coluna, e a quitação é decidida comparando o acumulado com o valor de `_expected_consultation_amount`. O saldo mostrado ao paciente passa a sair do acumulado, inclusive nos textos injetados no prompt.

**Tech Stack:** Python, FastAPI, LangGraph, Supabase (PostgREST + função SQL via rpc), pytest.

**Spec:** docs/superpowers/specs/2026-11-16-comprovantes-complementares-design.md

## Global Constraints

O valor total da consulta vem só de `_expected_consultation_amount` (app/graph/tools.py), que já embute o desconto de R$50 do PIX e a exceção do `custom_price`. Nenhum valor dito pela conversa pode mudar o total.

A soma só cobre PIX por agora. Cartão, dinheiro, link e cortesia continuam quitando direto, como hoje, com autoridade da atendente.

Quando o acumulado passa do total, a Eva marca quitada e avisa a clínica do excedente. Nunca move dinheiro sozinha e nunca mostra saldo negativo ao paciente.

`amount_paid_cents` é inteiro, em centavos, padrão zero. A conversão do valor pago é `round(amount_float * 100)`.

A soma tem que ser atômica, por função no banco (`col = col + x`), para não perder parcela numa corrida entre painel e Eva.

Testes: `uv run pytest --tb=short`. Um arquivo por camada. Tests novos de pagamento vão em `tests/test_tools.py`; não passar `test_patients.py` como primeiro argumento (import circular).

## Review Focus

Segundo PIX complementar que fecha a conta: o acumulado tem que alcançar o total, marcar `paid_at` e zerar o saldo, sem virar outro "Pagamento Parcial". Coberto na Task 2.

Parcela que ultrapassa o total: marca quitada, avisa a clínica do excedente e mostra saldo zero, nunca negativo. Coberto na Task 2.

Corrida painel e Eva somando ao mesmo tempo: nenhuma parcela se perde. Coberto pela função atômica, Task 1, e pelo teste de incremento, Task 2.

Primeira consulta dividida em duas linhas: o total do pacote é gravado igual nas duas e lido de uma só, sem somar dobrado. Coberto na Task 2.

Reenvio do mesmo comprovante, md5 repetido, valor ilegível: continuam não somando nada no acumulado. Coberto na Task 2.

---

### Task 1: Coluna acumuladora e função atômica de soma

**Files:**
- Create: `supabase/migrations/20261116_appointments_amount_paid_cents.sql`

**Interfaces:**
- Produces: coluna `appointments.amount_paid_cents INTEGER NOT NULL DEFAULT 0`; função SQL `increment_amount_paid(p_appointment_id text, p_cents integer) returns integer` que soma `p_cents` na linha daquele `appointment_id` e devolve o novo valor; função SQL `set_amount_paid(p_appointment_id text, p_cents integer) returns integer` que grava o valor absoluto e o devolve.

- [ ] **Step 1: Escrever a migration**

```sql
ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS amount_paid_cents INTEGER NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION increment_amount_paid(p_appointment_id text, p_cents integer)
RETURNS integer AS $$
  UPDATE appointments
     SET amount_paid_cents = amount_paid_cents + p_cents
   WHERE appointment_id = p_appointment_id
  RETURNING amount_paid_cents;
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION set_amount_paid(p_appointment_id text, p_cents integer)
RETURNS integer AS $$
  UPDATE appointments
     SET amount_paid_cents = p_cents
   WHERE appointment_id = p_appointment_id
  RETURNING amount_paid_cents;
$$ LANGUAGE sql;
```

- [ ] **Step 2: Aplicar a migration no banco de desenvolvimento e conferir**

Run: aplicar o arquivo SQL no Supabase do projeto (mesmo caminho das outras migrations).
Expected: a coluna existe com default 0 e as duas funções respondem via `client.rpc`.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20261116_appointments_amount_paid_cents.sql
git commit -m "feat(payments): coluna amount_paid_cents e funcoes atomicas de soma"
```

---

### Task 2: Acúmulo e quitação no register_payment da Eva

**Files:**
- Modify: `app/graph/tools.py` (função `register_payment`, trecho de decisão de tipo ~4988-5027; o cálculo de `expected`/`expected_remaining` ~4846-4853)
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `increment_amount_paid` (Task 1), `_parse_brl_amount`, `_expected_consultation_amount`, `_update_appts`, `_notify_clinic`, `log_event`.
- Produces: helper `async def _increment_amount_paid(client, appointment_ids: list[str], cents: int) -> int` em `app/graph/tools.py`, que chama a rpc `increment_amount_paid` para cada id da lista com o MESMO `cents` e devolve o acumulado da primeira linha (as linhas ligadas são mantidas iguais). Novo retorno de `register_payment` nos ramos de parcela e quitação usando o saldo real.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_tools.py
async def test_segundo_pix_complementar_quita_a_consulta(monkeypatch):
    # consulta R$650 (dinheiro/PIX R$600), taxa R$100 já paga, 1ª parcela R$300 já somada.
    # acumulado atual = 400; chega 2ª parcela de R$200 -> acumulado 600 == expected.
    # _increment_amount_paid devolve 60000 (centavos).
    # espera: payment_type "Consulta", paid_at setado, mensagem de QUITADA, sem saldo.
    ...
    assert "QUITADA" in result
    assert updated_fields.get("paid_at") is not None

async def test_parcela_intermediaria_mostra_saldo_real(monkeypatch):
    # acumulado após a parcela = 40000 (R$400) de um expected de 60000 (R$600).
    # espera: payment_type "Pagamento Parcial", saldo R$ 200,00, sem paid_at.
    assert "Pagamento Parcial" in payment_type
    assert "200" in result
    assert "paid_at" not in updated_fields

async def test_pagamento_a_mais_quita_e_avisa_clinica(monkeypatch):
    # acumulado após a parcela = 70000 (R$700) de expected 60000 (R$600).
    # espera: payment_type "Consulta", paid_at setado, saldo NUNCA negativo na mensagem,
    # _notify_clinic chamado com o excedente de R$ 100,00.
    assert "-" not in _saldo_shown
    assert notify_called_with_excedente == "100"

async def test_reenvio_md5_repetido_nao_soma(monkeypatch):
    # comprovante repetido (md5) -> retorno de bloqueio; _increment_amount_paid NUNCA chamado.
    assert increment_calls == 0

async def test_primeira_consulta_dividida_soma_igual_nas_duas_linhas(monkeypatch):
    # duas linhas ligadas; _increment_amount_paid recebe as duas ids e o mesmo cents.
    assert set(increment_ids) == {"id_parte1", "id_parte2"}
```

- [ ] **Step 2: Rodar os testes e ver falhar**

Run: `uv run pytest tests/test_tools.py -k "complementar or saldo_real or a_mais or md5_repetido or dividida" -v`
Expected: FAIL (helper e lógica ainda não existem).

- [ ] **Step 3: Implementar `_increment_amount_paid` e a nova decisão de tipo**

Criar `_increment_amount_paid(client, appointment_ids, cents)` que itera os ids chamando `client.rpc("increment_amount_paid", {"p_appointment_id": aid, "p_cents": cents})` e devolve o acumulado da primeira linha.

Na decisão de tipo de `register_payment`, trocar a base do saldo. O ramo da taxa (amount ≈ 100, taxa ainda não paga) continua rotulando "Taxa de Reserva" e setando `booking_fee_paid_at`, mas agora também soma `cents` no acumulado e calcula o saldo como `max(0, expected - acumulado)`. Para os demais comprovantes PIX (não `is_link`, não `payment_method`), somar `cents` no acumulado via helper, obter o novo acumulado, e então: se acumulado em reais ≥ `expected`, tipo "Consulta", setar `paid_at` e `booking_fee_paid_at`, e se acumulado > `expected`, chamar `_notify_clinic` com o excedente `acumulado - expected`; senão tipo "Pagamento Parcial", setar `booking_fee_paid_at`, saldo `expected - acumulado`. A mensagem ao paciente nunca mostra saldo abaixo de zero. Os ramos de dedup por drive_link, md5 repetido, reenvio da taxa na janela e valor ilegível retornam antes e não chamam o helper. Os ramos `is_link`/`payment_method` e cortesia são tratados na Task 3 e aqui ficam fazendo o set absoluto para `expected` (quitada direto), sem passar pela trava.

- [ ] **Step 4: Rodar os testes e ver passar**

Run: `uv run pytest tests/test_tools.py -k "complementar or saldo_real or a_mais or md5_repetido or dividida" -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte de pagamento existente**

Run: `uv run pytest tests/test_tools.py -k "payment or register" -v`
Expected: PASS (parcial antigo, saldo de consulta antiga, reenvio da taxa, cortesia continuam passando; ajustar os que mediam saldo pela conta antiga).

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(payments): soma PIX complementares e quita pelo valor rigido"
```

---

### Task 3: Acúmulo nos pagamentos de autoridade da atendente

**Files:**
- Modify: `app/graph/tools.py` (ramos `is_link`/`payment_method` ~4938-4953 e cortesia ~4865-4870 de `register_payment`)
- Modify: `dashboard/payments.py` (`mark_paid` ~762-794)
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `set_amount_paid` e `increment_amount_paid` (Task 1), `_expected_consultation_amount`.
- Produces: comportamento em que uma quitação por autoridade da atendente deixa `amount_paid_cents` coerente com a consulta quitada.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_tools.py
async def test_pagamento_link_seta_acumulado_cheio(monkeypatch):
    # is_link=True -> paid_at setado e set_amount_paid chamado com expected em centavos.
    assert set_amount_paid_called_with == expected_cents

async def test_cortesia_nao_fica_com_saldo_pendente(monkeypatch):
    # custom_price==0 -> quita; acumulado setado para expected (0 se cortesia pura).
    ...

# tests/test_dashboard.py (ou o arquivo de testes do painel existente)
async def test_painel_taxa_soma_cem(monkeypatch):
    # mark_paid tipo="taxa" -> increment_amount_paid com 10000.
    assert increment_called_with == 10000

async def test_painel_consulta_seta_acumulado_e_paid_at(monkeypatch):
    # mark_paid tipo="consulta" valor=650 -> paid_at setado e set_amount_paid com 65000.
    assert paid_at_set and set_amount_paid_called_with == 65000
```

- [ ] **Step 2: Rodar os testes e ver falhar**

Run: `uv run pytest tests/test_tools.py tests/test_dashboard.py -k "link or cortesia or painel" -v`
Expected: FAIL

- [ ] **Step 3: Implementar**

Em `register_payment`, nos ramos `is_link`/`payment_method` e cortesia, após setar `paid_at`, chamar `set_amount_paid` com `expected` em centavos (cortesia pura fica 0, pois `expected` é 0). Em `mark_paid` do painel, para `tipo == "taxa"` chamar `increment_amount_paid` com `valor*100` nas linhas; para `tipo == "consulta"` chamar `set_amount_paid` com `valor*100` nas linhas, além do update de `paid_at` que já existe. Usar o mesmo laço de `appointment_ids` já presente.

- [ ] **Step 4: Rodar os testes e ver passar**

Run: `uv run pytest tests/test_tools.py tests/test_dashboard.py -k "link or cortesia or painel" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py dashboard/payments.py tests/
git commit -m "feat(payments): painel e pagamentos da atendente alimentam o acumulado"
```

---

### Task 4: Saldo real no texto que o paciente lê

**Files:**
- Modify: `app/database.py` (`get_upcoming_appointments` ~434, incluir `amount_paid_cents` no select e no dict devolvido)
- Modify: `app/graph/nodes.py` (bloco das consultas ~3259-3314, acrescentar o saldo real por consulta)
- Modify: `app/graph/prompts.py` (tabelas de saldo ~944-953 e ~1008-1017)
- Test: `tests/test_process_message.py`

**Interfaces:**
- Consumes: `amount_paid_cents` de `get_upcoming_appointments`, `_expected_consultation_amount`.
- Produces: tag de saldo real ao lado de cada consulta com pagamento parcial, injetada no system prompt.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_process_message.py
async def test_prompt_mostra_saldo_real_com_parcial(monkeypatch):
    # consulta com amount_paid_cents=40000 e expected R$600 -> o system_prompt
    # injetado traz "saldo restante: R$ 200,00" para aquela consulta.
    assert "saldo restante: R$ 200,00" in system_prompt
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `uv run pytest tests/test_process_message.py -k "saldo_real" -v`
Expected: FAIL

- [ ] **Step 3: Implementar**

Em `get_upcoming_appointments`, somar `amount_paid_cents` ao select e ao dict. Em `nodes.py`, no laço que monta as linhas de consulta, quando `amount_paid_cents > 0` e a consulta ainda não está quitada, calcular `restante = expected - amount_paid_cents/100` com `_expected_consultation_amount` e acrescentar à `label` um sufixo tipo ` (saldo restante: R$ {restante},00)`. Nas tabelas de `prompts.py`, acrescentar uma linha dizendo que, quando houver saldo restante indicado ao lado de uma consulta específica, esse valor prevalece sobre a tabela, porque já houve pagamento parcial.

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `uv run pytest tests/test_process_message.py -k "saldo_real" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py app/graph/nodes.py app/graph/prompts.py tests/test_process_message.py
git commit -m "feat(payments): saldo real por consulta no prompt da Eva"
```

---

### Task 5: Preenchimento dos registros antigos

**Files:**
- Create: `scripts/_backfill_amount_paid_cents_oneoff.py`

**Interfaces:**
- Consumes: `_expected_consultation_amount` (ou a mesma regra de preço), as funções de conexão do projeto, o padrão idempotente dos outros `scripts/_backfill_*_oneoff.py`.

- [ ] **Step 1: Escrever o script em modo de teste (dry-run)**

O script percorre `appointments` com pagamento e calcula o `amount_paid_cents` esperado. Para linhas com `paid_at`, o valor é `expected` em centavos. Para linhas só com `booking_fee_paid_at` (sem `paid_at`), o valor é 10000, de forma conservadora. Em dry-run, só imprime o que gravaria, sem escrever. Idempotente: pode rodar de novo sem dobrar.

- [ ] **Step 2: Rodar em dry-run e conferir a amostra**

Run: `uv run python -m scripts._backfill_amount_paid_cents_oneoff --dry-run`
Expected: lista coerente, quitadas com o valor cheio, taxas com 100, sem erro.

- [ ] **Step 3: Rodar de verdade**

Run: `uv run python -m scripts._backfill_amount_paid_cents_oneoff`
Expected: linhas atualizadas; uma segunda execução não muda nada (idempotente).

- [ ] **Step 4: Commit**

```bash
git add scripts/_backfill_amount_paid_cents_oneoff.py
git commit -m "chore(payments): backfill de amount_paid_cents dos registros antigos"
```

---

## Notas de auto-revisão

Cobertura da spec: coluna e soma atômica na Task 1; acúmulo e quitação pelo valor rígido na Task 2; painel e pagamentos da atendente na Task 3; saldo real no prompt na Task 4; preenchimento antigo na Task 5. Remarcação não tem task porque o desenho concluiu que a coluna se preserva sozinha na atualização da mesma linha e a recolha da taxa nasce em linha nova com zero.

Pagamento misto (parte cartão) ficou fora por decisão da Ayexa. Se surgir, volta como spec nova.
