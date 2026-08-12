# Lembretes só para o contato próprio do paciente adulto

**Data:** 2026-08-12
**Status:** Aprovado para planejamento

## Problema

Hoje os crons de lembrete disparam para **todos** os contatos vinculados a um
paciente. Um paciente adulto que já tem o próprio número cadastrado ainda vê
lembretes de consulta/retorno chegando também no número de um responsável (pai,
mãe, cônjuge). E os lembretes de cobrança da taxa de reserva vão para todos os
contatos "financeiro", em vez de só para quem de fato fez aquela reserva.

## Modelo de dados (contexto)

- Ao cadastrar/agendar, o contato é vinculado ao paciente nos **três papéis**
  (`agendamento`, `financeiro`, `consulta`) com o **mesmo `is_self`**. Portanto
  `is_self=True` = o número do próprio paciente; `is_self=False` = um responsável.
- `appointments.contact_id` guarda **o contato que criou aquele agendamento**
  ("contato que realizou o agendamento").
- `patients.birth_date` define a idade; todo o código trata `age < 18` como menor
  (logo, adulto = `age >= 18`). `birth_date` convive em `dd/mm/aaaa` e ISO.

## Regras

### Regra A — Lembretes de consulta e de retorno

Afeta `scripts/send_appointment_reminders.py` (véspera + dia) e
`scripts/send_return_reminders.py` (retorno periódico), que hoje enviam para
todos os contatos de papel `consulta`.

- Se o paciente é **adulto (`age >= 18`)** **e** tem ao menos um contato
  `is_self=True` → enviar **somente** aos contatos `is_self=True`; descartar os
  responsáveis.
- Caso contrário (menor de idade, sem contato próprio, ou `birth_date` ausente/
  não parseável) → comportamento **idêntico ao de hoje**: enviar para todos.

`birth_date` ausente **não** suprime ninguém — na dúvida, entrega para todos
(garante que o lembrete transacional chegue). A cobrança de coletar a data de
nascimento no fluxo fica para tarefa separada, fora deste escopo.

### Regra B — Lembretes de pagamento (taxa de reserva)

Afeta `scripts/send_payment_reminders.py`, que hoje envia para todos os contatos
`financeiro`.

- O lembrete vai **somente para `appointments.contact_id`** (o contato que fez a
  reserva), independentemente da idade do paciente.
- A **notificação de cancelamento automático** ("liberamos a vaga") é a mensagem
  terminal do mesmo fluxo de cobrança — segue a mesma regra: vai só para o
  `contact_id`. O portão de segurança "só cancela se ao menos um contato foi
  notificado" continua valendo (agora sobre o contato da reserva).
- Fallback: se `contact_id` for nulo (agendamentos antigos/remarcações que não
  gravaram o contato), manter o comportamento atual (contatos `financeiro`).
- **Nuance de segurança:** a mudança é só de **destinatário do lembrete**. A
  detecção de comprovante que bloqueia cancelamento automático indevido
  (`find_receipt_in_conversation`) **continua varrendo todos os contatos
  financeiros** — um comprovante pode chegar por qualquer número vinculado, e
  estreitar essa busca reintroduziria o risco de cancelar consulta já paga
  (caso Bernardo Lima Beltrão Teixeira, citado no próprio script).

### Fora de escopo

- `scripts/send_pending_payments_reminder.py` — monta um **e-mail interno para a
  clínica**, não envia mensagem ao paciente; não se aplica. (Já prefere o
  contato `is_self` na exibição.)
- Coletar `birth_date` antes de qualquer agendamento (Regra C descartada desta
  entrega, vira tarefa separada).
- Scripts one-off `scripts/_*.py`.

## Arquitetura

Centralizar a lógica em `app/patients.py`, para não duplicar a regra em cada
cron:

### `_compute_age(birth_date: str | None) -> int | None`
Função pura. Parseia `dd/mm/aaaa` ou ISO (reutiliza a tolerância de
`_birth_date_variants`), devolve a idade em anos completos, ou `None` se ausente/
não parseável.

### `get_reminder_contacts(patient_id, role, include_inactive=True) -> list[dict]`
Novo helper que aplica a Regra A. Faz um único fetch de `patient_contacts` do
papel com `is_self` + `contacts(*)`, mais o `birth_date` do paciente. Se adulto e
existir ao menos um `is_self=True`, retorna só os `is_self=True`; senão retorna
todos (mesma semântica de `active`/`include_inactive` de
`get_contacts_for_patient`). Não altera `get_contacts_for_patient` (usada em
outros fluxos em `tools.py`).

### `get_contact_by_id(contact_id: str) -> dict | None`
Novo helper para a Regra B: resolve `phone`/`name` do contato que fez a reserva.

### Mudanças nos crons
- `send_appointment_reminders.py`: troca `get_contacts_for_patient(patient_id,
  "consulta", ...)` por `get_reminder_contacts(patient_id, "consulta", ...)`.
- `send_return_reminders.py`: mesma troca no `_send_for_row`.
- `send_payment_reminders.py`: destinatário do lembrete passa a ser
  `get_contact_by_id(appt["contact_id"])` (com fallback para
  `get_financial_contacts` se nulo); `find_receipt_in_conversation` **mantém** a
  lista ampla de contatos financeiros.

## Fluxo de dados

```
cron consulta/retorno → get_reminder_contacts(patient_id, "consulta")
    → fetch patient_contacts(role=consulta, is_self, contacts) + patients.birth_date
    → adulto & tem self?  sim → [contatos is_self]   não → [todos]
    → envia template a cada telefone

cron taxa → para cada appointment:
    destinatário = get_contact_by_id(appt.contact_id) or get_financial_contacts(...)
    receipt-guard = find_receipt_in_conversation(TODOS os financeiros)  # inalterado
```

## Tratamento de erros / edge cases

- `birth_date` ausente ou imparseável → não suprime (entrega para todos).
- Adulto sem nenhum contato `is_self` → entrega para todos (não deixa o paciente
  sem lembrete).
- Adulto com self, mas o self está inativo/pausado e `include_inactive=True` →
  entrega ao self mesmo assim (lembrete transacional; mesma política de hoje).
- `appointments.contact_id` nulo → fallback financeiro.
- `contact_id` aponta para um responsável (ex.: mãe agendou p/ filho adulto) →
  correto: o lembrete de pagamento vai para quem reservou.

## Testes

Arquivos existentes: `tests/test_reminders.py`,
`tests/test_return_reminders_cron.py`, `tests/test_payment_reminders_cancel.py`.

- **Regra A (unit em `get_reminder_contacts` + integração mockada nos crons):**
  - adulto (25a) com self + responsável → só o self.
  - adulto sem self (só responsável) → todos.
  - menor (10a) com self + responsável → todos.
  - `birth_date` nulo / imparseável → todos.
- **Regra B (`send_payment_reminders`):**
  - `contact_id` presente → envia só para esse contato.
  - `contact_id` nulo → fallback para contatos financeiros.
  - comprovante enviado por um contato financeiro que **não** é o `contact_id`
    ainda bloqueia o cancelamento (guard permanece amplo).
- **`_compute_age`:** unit para `dd/mm/aaaa`, ISO, nulo, string inválida, e o
  limite exato de 18 anos.
```
