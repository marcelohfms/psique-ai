# Cobrança da taxa de reserva a paciente cortesia (Lucas / Silvana)

## O que aconteceu

Conversa de 11/08/2026, contato **Silvana Kárin de Oliveira Pereira e Silva**
(`5581973460726`), agendando o filho **Lucas Raphael de Oliveira Pereira e Silva**
com o Dr. Júlio para 27/08/2026 18:00.

Lucas é **paciente cortesia**: `patients.custom_price == 0` — a consulta é
inteiramente gratuita e não deve nenhuma taxa de reserva.

Mesmo assim:

- Às **12:55** o cron `send_payment_reminders` enviou o lembrete de cobrança da
  taxa de reserva e marcou `payment_reminder_sent_at`.
- Às **13:20** a Eva reforçou: *"a vaga só fica garantida após o pagamento da taxa
  de reserva"*.
- O agendamento (`aqvtvveb46jp0bi0v7fpu6lt18`) ficou a **~35 min do
  auto-cancelamento** por falta de pagamento (o cron cancela 2h após o lembrete).

A consulta anterior de Lucas (16/07) só escapou porque tinha
`booking_fee_waived=True`, marcado na mão.

## Causa raiz

`scripts/send_payment_reminders.py` decidia quem cobrar/cancelar olhando apenas
`booking_fee_waived == False` e `booking_fee_paid_at is null`. **Nunca** consultava
`patients.custom_price`. Cortesia (`custom_price == 0`) é um conceito diferente de
taxa dispensada (`booking_fee_waived` numa consulta que ainda tem preço) — e o cron
não conhecia o primeiro.

O agendamento nasceu com `booking_fee_waived=False` (foi criado fora do fluxo do
bot — sem evento `appointment_booked`), então entrou nas duas queries do cron como
se fosse um paciente comum devendo R$ 100,00.

## Correção

Guarda de cortesia no cron, cobrindo **todos** os caminhos de criação do
agendamento (bot, dashboard, atendente):

- `_is_courtesy(appt)` — retorna `True` quando `patients.custom_price == 0`.
- `_send_payment_reminder` e `_cancel_unpaid_appointment` retornam logo no início
  (antes de buscar contatos) quando o paciente é cortesia.
- A query de `main()` passou a trazer `patients(name, custom_price)`.

Testes: `test_reminder_skipped_for_courtesy_patient` e
`test_cancel_skipped_for_courtesy_patient` em
`tests/test_payment_reminders_cancel.py`.

## Ação manual no banco (11/08/2026)

Para conter o auto-cancelamento iminente, `booking_fee_waived` do appointment
`aqvtvveb46jp0bi0v7fpu6lt18` foi setado para `True`
(`scripts/_fix_lucas_cortesia_waive.py`). Com a guarda de cortesia no cron isso
passa a ser redundante, mas foi o que segurou a vaga no momento.

## Pendência para a clínica

A Silvana foi informada por engano de que precisava pagar a taxa. Cabe à clínica
avisá-la de que, por ser cortesia, **não há taxa a pagar** — a Eva não corrige isso
sozinha.
