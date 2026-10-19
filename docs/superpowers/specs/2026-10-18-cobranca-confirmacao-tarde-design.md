# Segunda cobrança de confirmação na tarde da véspera

Data: 2026-10-18
Branch: `confirmacao-tarde-vespera`

## Problema

Hoje a Eva pede confirmação de presença uma vez só, no lembrete de véspera que
sai de manhã (mira 7h de Recife, janela de tentativa até 12h). Pacientes que não
respondem de manhã simplesmente não recebem nenhum novo toque antes do dia da
consulta. A clínica quer um segundo lembrete, à tarde, só para quem ainda não
confirmou.

## Comportamento

Uma segunda cobrança de confirmação, enviada no máximo uma vez por consulta, na
tarde da véspera. Mira as 14h de Recife, com janela de tentativa até as 18h
(mesmo padrão da manhã: absorve atraso do agendador ou restart, já que o
`_sent_at` só é gravado no sucesso).

O disparo só acontece para uma consulta que satisfaça todas estas condições:

1. A consulta é no dia seguinte (mesmo alvo do lembrete da manhã).
2. O lembrete da manhã já saiu (`reminder_day_before_sent_at` preenchido). Sem
   isso, não existe "primeira cobrança" a reforçar.
3. O paciente ainda não confirmou presença (`confirmed_at` vazio).
4. A segunda cobrança ainda não foi enviada (`confirmation_nudge_sent_at` vazio).
5. Horário local entre 14h e 18h (`14 <= now.hour < 18`).

Quem confirmar entre a manhã e a tarde não recebe a segunda. Quem não responder à
segunda não recebe mais nada: não há terceira cobrança nem toque no dia da
consulta (o lembrete `lembrete_dia_consulta` continua como está, é outro fluxo e
não pede confirmação).

Os contatos que recebem seguem a mesma regra de idade já usada nos lembretes
(`consultation_reminder_contacts`), e cada envio é espaçado por
`BATCH_PAUSE_SECONDS`, idêntico aos blocos existentes.

## Onde fica no código

Tudo em `scripts/send_appointment_reminders.py`, que já roda de 10 em 10 minutos
pelo agendador (`app/scheduler.py`, job `appointment_reminders`). O script já tem
dois blocos — véspera de manhã e dia da consulta. Adiciona-se um terceiro bloco,
a cobrança da tarde, reaproveitando `_send_reminder_to_contacts` (mesma função
que monta o template, resolve contatos e grava o `_sent_at`).

Não há job novo no agendador: o job existente já acorda a tempo e o novo bloco só
age dentro da sua própria janela de horário.

A query do novo bloco espelha a do bloco da manhã, trocando o filtro:
- `start_time` cai amanhã (reusa `tomorrow_start` / `tomorrow_end`);
- `reminder_day_before_sent_at` NÃO é nulo;
- `confirmed_at` é nulo;
- `confirmation_nudge_sent_at` é nulo.

O envio usa `sent_col = "confirmation_nudge_sent_at"`, que `_send_reminder_to_contacts`
grava com `now.isoformat()` só quando o envio deu certo.

## Banco

Coluna nova em `appointments`:

```sql
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmation_nudge_sent_at timestamptz;
```

Gravada só no sucesso do envio, igual a `reminder_day_before_sent_at`. É a trava
de idempotência que garante no máximo uma cobrança por consulta. A migração entra
no mesmo lugar onde as outras colunas `_sent_at` estão documentadas (cabeçalho do
script) e deve ser aplicada em produção antes do deploy valer.

## Template Meta

Template novo, categoria UTILITY, idioma `pt_BR`, com três variáveis na mesma
ordem dos outros (nome, médico, horário). Tom de reforço, por exemplo:

> Olá! Ainda não recebi a confirmação de presença de {{1}} na consulta de amanhã
> com {{2}} às {{3}}. Consegue confirmar pra mim?

Nome do template a definir ao criar na Meta; o código referencia esse nome no
novo bloco e no mapa de mensagem em texto (`_plain_message`). Enquanto a Meta não
aprovar o template, o envio falha, o `confirmation_nudge_sent_at` não é gravado e
o ciclo seguinte tenta de novo. Ou seja, publicar o código antes da aprovação não
quebra nada — a cobrança só começa a sair quando o template existir.

## Testes

Em `tests/test_reminders.py`, que já cobre `send_appointment_reminders.py`. Casos
do novo bloco, com `now` fixado na tarde:

1. Dispara quando a consulta é amanhã, manhã já saiu, não confirmou, não cobrou
   ainda e está na janela (14h–18h).
2. Não dispara se `confirmed_at` já está preenchido.
3. Não dispara se `confirmation_nudge_sent_at` já está preenchido.
4. Não dispara se `reminder_day_before_sent_at` está nulo (manhã nem saiu).
5. Não dispara fora da janela (antes das 14h e às 18h ou depois).
6. No sucesso, grava `confirmation_nudge_sent_at`.

Os blocos existentes (manhã e dia da consulta) não podem mudar de comportamento;
os testes atuais devem continuar passando.

## Fora de escopo

- Terceira cobrança ou toque no dia da consulta.
- Mudar o lembrete da manhã ou o do dia da consulta.
- Qualquer alteração no fluxo de `confirm_attendance`.
