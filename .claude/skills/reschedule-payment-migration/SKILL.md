---
name: reschedule-payment-migration
description: Use sempre que alterar manualmente a data/hora de uma consulta via script one-off ou dashboard (fora do fluxo normal do bot) — garante que taxa/pagamento já registrados migrem junto e que não sobre um agendamento duplicado ativo. Gatilhos — "mude a data dessa consulta", "reagende esse paciente", "criei outro agendamento pra remarcar", ou qualquer script scripts/_reschedule_*.py / scripts/_check_*.py que crie um novo appointment_id para um paciente que já tinha um agendamento pago.
---

# Migração de pagamento ao alterar agenda manualmente

Caso real (Vicente Sales da Nóbrega, 2026-07-29): um agendamento (`b325f858...`,
13/08) recebeu o comprovante da taxa de reserva às 13:58. Depois, a data foi trocada
para 10/08 criando um **novo** appointment_id (`gcgu8m9o...`) em vez de só migrar
data/hora no registro existente. O antigo continuou `scheduled` (duplicado ativo) e
o novo nasceu com `booking_fee_paid_at = null` — 2h depois o cron de lembretes
(`scripts/send_payment_reminders.py`) disparou cobrança para uma consulta cujo
paciente já tinha pago, e o duplicado teria sido auto-cancelado 2h depois disso.

## Regra

Toda vez que você alterar a data/hora de uma consulta de um jeito que **não** passa
pelo par de ferramentas do bot (`mark_reschedule_in_progress` +
`reschedule_appointment` em `app/graph/tools.py`) — ou seja, qualquer script one-off
ou ação de dashboard que mexa direto na tabela `appointments` — siga este checklist:

1. **Prefira UPDATE na mesma linha** (mudar só `start_time`/`end_time`) em vez de
   criar um `appointment_id` novo. É isso que `reschedule_appointment` faz no caminho
   normal (linha 1652-1672 de `app/graph/tools.py`) — como só campos específicos são
   atualizados, `booking_fee_paid_at`, `paid_at` e `booking_fee_waived` são
   preservados automaticamente, sem precisar de nenhuma migração manual.

2. **Se for inevitável criar uma linha nova** (ex.: precisa de um `appointment_id`
   novo do Google Calendar), antes de criar, leia o agendamento antigo e confira:
   - `booking_fee_paid_at`
   - `paid_at`
   - `booking_fee_waived`

   Copie os valores não nulos para a linha nova.

3. **Cancele o agendamento antigo** (`status = "canceled"`) — nunca deixe os dois
   como `scheduled` ao mesmo tempo. Se o `appointment_id` antigo seguir o formato do
   Google Calendar (não um UUID solto), tente `cancel_event` também (best-effort,
   `try/except` — pode já não existir).

4. **Não duplique o registro financeiro.** O pagamento já foi lançado na planilha
   Google Sheets e por e-mail para a clínica quando foi originalmente registrado
   (`dashboard/payments.py::mark_paid`). Migrar o campo no banco é suficiente — **não**
   chame `mark_paid`/`_append_payment_sheet` de novo para a linha nova, isso criaria
   uma segunda linha na planilha para a mesma taxa.

5. **Registre um evento de auditoria** com `log_event` (`app/database.py`), tipo
   `attendant_payment_migrated_after_reschedule`, contendo `old_appointment_id`,
   `new_appointment_id` e o timestamp migrado — para rastrear que foi uma migração
   manual, não um pagamento novo.

Ver `scripts/_fix_vicente_payment_migration_oneoff.py` como referência de
implementação desse checklist.
