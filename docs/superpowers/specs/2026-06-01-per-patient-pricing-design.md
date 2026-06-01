# Design: Exceções de Preço por Paciente

**Data:** 2026-06-01
**Status:** Aprovado

## Contexto

Alguns pacientes têm condições de preço diferenciadas: valor de consulta especial (ex: R$500 em vez do padrão) ou cortesia (R$0). Outros têm a taxa de reserva dispensada pela clínica, independentemente do valor da consulta. Atualmente o sistema só suporta preços globais definidos em `prompts.py`.

## Escopo

- Armazenamento das exceções no Supabase (configuração manual via dashboard)
- Injeção do preço especial no system prompt de Eva
- Taxa de reserva: dispensada automaticamente para cortesias; configurável independentemente para demais exceções
- Validação correta de pagamentos no `register_payment`
- E-mail de pendências: exclui pacientes com taxa dispensada e cortesias

## Modelo de Dados

### Tabela `users` — dois novos campos

| Campo | Tipo | Padrão | Descrição |
|---|---|---|---|
| `custom_price` | `INTEGER NULL` | `NULL` | `NULL` = preço padrão; `0` = cortesia; inteiro positivo = preço especial |
| `booking_fee_waived` | `BOOLEAN` | `false` | Dispensa a taxa de reserva de R$100 |

Configurados manualmente pela atendente/admin no dashboard do Supabase.

**Combinações válidas:**

| `custom_price` | `booking_fee_waived` | Comportamento |
|---|---|---|
| NULL | false | Padrão — sem exceção |
| NULL | true | Preço padrão da clínica, sem taxa de reserva |
| 500 | false | R$500 de consulta, taxa normal de R$100 |
| 500 | true | R$500 de consulta, sem taxa de reserva |
| 0 | true | Cortesia total — sem nenhum valor a pagar |

O preço customizado é o valor final negociado — **não se aplica desconto PIX** sobre ele.

### Tabela `appointments` — um novo campo

| Campo | Tipo | Padrão | Descrição |
|---|---|---|---|
| `booking_fee_waived` | `BOOLEAN` | `false` | Cópia do valor do usuário no momento do agendamento |

Copiado de `users.booking_fee_waived` por `confirm_appointment`. Garante que `register_payment` e o e-mail de pendências operem sem join com `users`.

## Arquitetura

### 1. Injeção no system prompt (`prompts.py` + `nodes.py`)

Nova função em `prompts.py`:

```python
def get_pricing_exception_rule(
    custom_price: int | None,
    booking_fee_waived: bool,
    standard_price: int,
) -> str
```

Retorna um bloco de texto que Eva segue em vez das regras padrão de preço/taxa. O `standard_price` é calculado em `nodes.py` com base em médico/idade/data atual (mesmo cálculo de `_expected_consultation_amount`), para que a mensagem de "valor integral no dia" já traga o número correto.

O bloco é appendado ao fim do system prompt em `nodes.py` quando `custom_price is not None` ou `booking_fee_waived is True`.

**Bloco: `booking_fee_waived=True`, `custom_price=NULL`**
```
⚠️ EXCEÇÃO PARA ESTE PACIENTE — sobrepõe qualquer regra de preço acima:
- A taxa de reserva está DISPENSADA para este paciente.
- Após confirm_appointment, envie EXATAMENTE:
  "Consulta registrada! ✅ Para você, a taxa de reserva está dispensada 😊
  O valor integral da consulta (R$ {standard_price},00) deverá ser pago no dia da consulta."
- NÃO envie instruções de PIX para taxa de reserva.
- Quando informar o preço, diga: "O seu valor especial para esta consulta é R$ {standard_price},00."
```

**Bloco: `custom_price=500`, `booking_fee_waived=False`**
```
⚠️ EXCEÇÃO PARA ESTE PACIENTE:
- Este paciente tem valor especial de R$ 500,00 por consulta.
- A taxa de reserva de R$ 100,00 se aplica normalmente (abatida do total).
- Quando informar o preço, diga: "O seu valor especial para esta consulta é R$ 500,00."
- NÃO mencione os valores padrão da clínica nem o reajuste de junho.
```

**Bloco: `custom_price=0`, `booking_fee_waived=True` (cortesia)**
```
⚠️ EXCEÇÃO PARA ESTE PACIENTE — cortesia:
- Esta consulta é cortesia, sem nenhum valor a pagar.
- Após confirm_appointment, envie: "Consulta registrada! ✅ Esta consulta é cortesia — nenhum valor será cobrado. 😊"
- NÃO envie nenhuma instrução de pagamento ou taxa.
- Se perguntado sobre preço, diga: "Para você, esta consulta é cortesia."
```

### 2. `confirm_appointment` (`tools.py`)

Após criar o agendamento no banco:

1. Carrega `booking_fee_waived` do usuário via `get_user_by_phone(phone)`
2. Salva `booking_fee_waived` no appointment recém-criado
3. Se `booking_fee_waived=True`: define `booking_fee_paid_at = now()` automaticamente — remove o appointment da seção "taxa pendente" do e-mail diário sem nenhuma lógica adicional no script

### 3. `_expected_consultation_amount` + `register_payment` (`tools.py`)

**`_expected_consultation_amount` — novo parâmetro:**
```python
def _expected_consultation_amount(
    doctor_key, patient_age, consultation_type, now_dt,
    price_override: int | None = None,
) -> int
```
Se `price_override is not None`: retorna `price_override` diretamente (sem cálculo padrão, sem desconto PIX).

**`register_payment` — ajustes:**

- Lê `custom_price` da tabela `users` via `phone` (já disponível em `config`)
- Lê `booking_fee_waived` do appointment (já buscado na query existente)
- Cálculo de `expected_remaining`:
  ```python
  if booking_fee_waived:
      expected_remaining = expected  # paciente nunca pagou a taxa
  else:
      expected_remaining = (expected - 100) if booking_fee_already_paid else expected
  ```
- Cortesia (`custom_price=0`, `expected=0`): retorna "consulta QUITADA" imediatamente

### 4. E-mail de pendências (`send_pending_payments_reminder.py`)

**Seção "Taxa de reserva pendente"** — adiciona filtro:
```python
.eq("booking_fee_waived", False)
```

**Seção "Pagamento de consulta pendente"** — filtra cortesias em Python após a query:
```python
consulta_pendente = [
    appt for appt in r2.data or []
    if (appt.get("users") or {}).get("custom_price") != 0
]
```
Requer `custom_price` no `.select(...)` via join com `users`.

## Fluxo completo — exemplo `custom_price=NULL`, `booking_fee_waived=True`

```
Paciente agenda consulta com Dr. Júlio (adulto, preço padrão R$650)
  → confirm_appointment:
      booking_fee_waived = True (lido de users)
      appointments.booking_fee_waived = True
      appointments.booking_fee_paid_at = now()   ← marca como dispensada
  → Eva envia ao paciente:
      "Consulta registrada! ✅ Para você, a taxa de reserva está dispensada 😊
       O valor integral da consulta (R$ 650,00) deverá ser pago no dia da consulta."
  → E-mail de pendências: NÃO aparece em "taxa pendente"
  → Após a consulta (status=completed, paid_at=NULL):
      Aparece em "pagamento de consulta pendente" normalmente
  → Paciente paga R$650 no dia e envia comprovante:
      register_payment: expected=650, booking_fee_waived=True
      expected_remaining = 650 (sem dedução de R$100)
      amount=650 ≈ expected_remaining → QUITADA ✅
```

## Tratamento de erros

- Se `get_user_by_phone` falhar em `confirm_appointment`: `booking_fee_waived` assume `False` (comportamento padrão seguro)
- Se `custom_price` não estiver na resposta do usuário: assume `None` (preço padrão)
- Cortesia (`custom_price=0`) que envia comprovante: registrado normalmente na planilha, retorna "QUITADA" imediatamente

## Testes

**`tests/test_tools.py`:**
- `test_confirm_appointment_copies_booking_fee_waived_to_appointment`: verifica que `booking_fee_waived` é copiado para o appointment e `booking_fee_paid_at` é definido quando `True`
- `test_expected_consultation_amount_price_override`: verifica que `price_override=500` retorna 500 sem desconto PIX
- `test_register_payment_booking_fee_waived_no_deduction`: verifica que `expected_remaining` não subtrai R$100 quando `booking_fee_waived=True`
- `test_register_payment_courtesy_zero_price`: verifica retorno "QUITADA" para `custom_price=0`

**`tests/test_process_message.py`:**
- `test_pricing_exception_block_injected_in_system_prompt`: verifica que o bloco de exceção aparece no system prompt quando `custom_price` ou `booking_fee_waived` estão ativos

## O que não muda

- Estrutura do `get_booking_fee_rule()`: mantida para pacientes sem exceção
- `_PRICING_BODY_PRE` / `_PRICING_BODY_POS`: sem alteração
- Fluxo de agendamento, cancelamento e reagendamento: sem alteração
- Taxa de reserva R$100 como padrão para todos os outros pacientes
