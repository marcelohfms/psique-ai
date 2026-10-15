# Confirmação de presença só na véspera

## Problema

A tool `confirm_attendance` ([app/graph/tools.py:3297](../../../app/graph/tools.py)) grava
`confirmed_at` sem olhar a data da consulta. O único filtro de "é véspera" vive na seleção de
quem recebe o lembrete do dia anterior (`scripts/send_appointment_reminders.py`), não na hora de
gravar a confirmação. A única barreira contra confirmar uma consulta distante é uma instrução no
prompt do LLM, que é probabilística. Resultado: uma mensagem afirmativa do paciente ("sim",
"confirmado", "ok") numa conversa onde há uma consulta futura listada pode marcar `confirmed_at`
numa consulta que ainda está a vários dias de distância.

Regra desejada: a consulta só pode ser confirmada com 1 dia de antecedência. Consultas mais
distantes não devem ter marcação de confirmação.

## Regra de corte

A confirmação de presença é permitida quando a data da consulta for **hoje ou amanhã** no fuso de
Recife (`TZ`, America/Recife). Em termos de distância: permitido quando
`data_da_consulta - data_de_hoje <= 1 dia`. Se faltarem 2 dias ou mais, a confirmação é recusada.

A comparação é feita por data de calendário (não por horas exatas), coerente com o lembrete da
véspera, que sai no dia anterior, e com o lembrete do próprio dia.

## Mudanças

### 1. Trava determinística em `confirm_attendance`

Arquivo: `app/graph/tools.py` (função `confirm_attendance`, ~3297-3406).

- Incluir `start_time` no SELECT que hoje traz apenas `confirmed_at, status, patient_id, doctor_id`.
- Após as guardas existentes (contato dono, status cancelada/completada, idempotência) e **antes**
  de gravar `confirmed_at`, calcular a distância em dias entre a data da consulta e hoje no fuso
  `TZ`.
- Se faltar 2 dias ou mais: **não** grava `confirmed_at`, **não** chama `mark_event_confirmed`, e
  retorna uma instrução interna para a Eva não afirmar que confirmou, pedindo que agradeça e diga
  que confirmará perto da data. Formato coerente com as outras instruções internas da tool.
- Se for hoje ou amanhã: comportamento atual, inalterado (grava `confirmed_at` + marca o Calendar).
- As demais guardas e a idempotência ("primeiro a confirmar vence") ficam iguais.

Observações de fuso: `start_time` vem do banco; a conversão para `TZ` deve seguir a convenção já
usada no arquivo para evitar o erro de `astimezone` sobre naive em UTC (ver histórico do caso Luna).
A distância é calculada sobre a **data** (`.date()`) no fuso de Recife, dos dois lados.

### 2. Reverter a marca de confirmado no Calendar

Arquivo: `app/google_calendar.py`.

A marca de confirmado é o prefixo `"✅ "` no título (`CONFIRMED_PREFIX`) mais a cor verde
(`CONFIRMED_COLOR_ID = "2"`). Eventos normais são criados sem `colorId` explícito, então "voltar ao
padrão" é limpar o `colorId`.

- Criar `_unmark_event_confirmed(service, calendar_id, event_id)` simétrica a
  `_mark_event_confirmed` ([app/google_calendar.py:653](../../../app/google_calendar.py)): lê o
  evento, remove o prefixo `"✅ "` do título se presente (preservando o resto, inclusive edições
  manuais da clínica), e limpa o `colorId` para voltar à cor padrão do calendário.
- Criar o wrapper assíncrono `mark_event_unconfirmed(calendar_id, event_id)` simétrico a
  `mark_event_confirmed` ([app/google_calendar.py:1048](../../../app/google_calendar.py)).

### 3. Script one-off de limpeza do passivo

Arquivo: `scripts/_limpar_confirmacoes_futuras_oneoff.py` (prefixo `_`, padrão dos one-offs).

- Buscar em `appointments` as linhas com `confirmed_at` preenchido, `status = "scheduled"` e
  `start_time` a 2 dias ou mais de distância de hoje (fuso Recife).
- Imprimir a lista (paciente, médico, data, appointment_id) para conferência antes de aplicar.
- Modo aplicação: para cada linha, zerar `confirmed_at` no banco e chamar `mark_event_unconfirmed`
  no evento correspondente (resiliente a falha do Calendar, como `confirm_attendance` já é ao
  marcar).
- Rodar com proteção de "dry-run primeiro" (listar; aplicar só com flag/confirmação explícita).

### 4. Testes

Arquivo: `tests/test_tools.py` e, se necessário, um teste para a função de Calendar.

- Novos casos em `confirm_attendance`:
  - recusa (não grava `confirmed_at`, não chama Calendar) quando a consulta está a vários dias.
  - aceita quando a consulta é amanhã.
  - aceita quando a consulta é hoje.
- Ajustar os mocks dos testes atuais de `confirm_attendance` (2194, 2212, 2249, 2286, 2369, 4418)
  para que a consulta mockada caia na janela permitida (hoje/amanhã), já que agora o `start_time` é
  lido e checado.
- Teste da nova função `mark_event_unconfirmed` / `_unmark_event_confirmed`: remove o prefixo e
  limpa o `colorId`.
- Garantir que a suíte passa: `uv run pytest --tb=short`.

## Fora de escopo (YAGNI)

- Não mexer na seleção do lembrete de véspera (já filtra por data corretamente).
- Não duplicar a trava na lista "Consultas agendadas" de `nodes.py` nem no prompt; a trava na tool
  é a causa raiz. O prompt existente permanece como guia probabilístico complementar.
- Não alterar `reschedule_appointment` (já zera `confirmed_at`).

## Riscos e pontos de atenção

- Fuso horário: calcular a data no fuso de Recife dos dois lados, sem `astimezone` sobre naive UTC.
- No-show / `complete_appointments.py` usa `confirmed_at` + `reminder_day_before_sent_at`. Limpar
  confirmações de consultas **futuras** não afeta esse fluxo (que olha consultas já passadas).
- Idempotência: a trava roda antes da gravação; a checagem de `confirmed_at` já existente continua
  válida para a janela permitida.
