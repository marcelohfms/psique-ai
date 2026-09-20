# Relatório de funil de leads

Data: 2026-09-20
Status: aprovado (design), aguardando plano de implementação.
Origem: `AUDITORIA-ESTRATEGICA-2026-09.md`, seção 12 (oportunidades não óbvias: funil de conversão não medido).

## Contexto e problema

O sistema já grava os eventos do funil na tabela `events` (`conversation_started`, `slots_offered`, `appointment_booked`, `payment_receipt_registered`, etc.), mas ninguém mede a conversão. A dona não sabe quantos leads chegam por semana, quantos viram consulta paga e quantos se perdem, nem em qual etapa perde mais. Este relatório fecha esse buraco. É read-only sobre `events` (e `messages` para a última atividade), roda como cron e não encosta no webhook nem no grafo.

## Objetivo

Um relatório periódico por e-mail com o funil de conversão do período e a situação dos leads que não converteram.

## Não-objetivos

- Não tocar o webhook, o grafo nem os crons existentes.
- Não criar dashboard nem UI; a entrega é um e-mail (mesmo padrão dos outros relatórios).
- Não criar tabela nova nem migration; usa `events` e `messages` que já existem.
- Não é monitoramento de infra (isso é outro projeto).

## Decisões tomadas no brainstorming

- Conversão só conta quando **pago**: um agendamento sem taxa paga NÃO é "agendado".
- Etapas do funil (nomes de negócio, não de evento):
  1. **Interessados** — iniciou conversa.
  2. **Qualificados** — concluiu o cadastro e recebeu horários.
  3. **Agendados** — agendou E pagou a taxa (ou é isento). Conversão.
- Quem não chegou a "Agendados" cai em:
  - **Pendentes** — em aberto e recente (teve atividade nos últimos 7 dias). Fila quente.
  - **Perdidos** — em aberto e frio (sem atividade há mais de 7 dias).
- Corte pendente/perdido: **7 dias** de atividade.

## Decisões a confirmar na revisão do spec (padrões sensatos)

- **Periodicidade:** semanal (segunda de manhã, horário de Recife). Pode virar diário/mensal.
- **Destinatário:** e-mail configurável via `FUNNEL_REPORT_EMAIL`, com fallback para `CLINIC_NOTIFY_EMAIL`. O público é a dona/gestão, não a recepção.
- **Janela do funil:** cohort de quem chegou nos **últimos 30 dias** (não 7), para a conversão e o "perdido" fazerem sentido. O corte de 7 dias continua sendo só o que separa pendente de perdido dentro desse cohort. Pode ser ajustado.

## Modelo de cálculo

### Cohort e fonte dos fatos (tudo read-only)

- **Cohort:** telefones cujo primeiro `conversation_started` está dentro da janela (30 dias). O "chegou" é a base do funil.
- **Eventos do funil** (tabela `events`, por telefone, dentro da janela):
  - Interessado: tem `conversation_started`.
  - Qualificado: tem `slots_offered`.
  - Agendado (pago): tem `appointment_booked` E ao menos um de `payment_receipt_registered`, `booking_fee_registered` ou `booking_fee_waived` (taxa resolvida).
- **Última atividade:** maior `created_at` entre as mensagens do telefone na tabela `messages` (fonte real de atividade do paciente). Usada só para separar pendente de perdido.

### Classificação por lead (função pura)

Para cada telefone do cohort, a etapa mais avançada alcançada:

- **Agendado** se tem agendamento pago (definição acima). É a conversão; não entra em pendente/perdido.
- Senão, é **não-convertido**, e se divide por atividade:
  - **Pendente** se a última mensagem foi há 7 dias ou menos.
  - **Perdido** se foi há mais de 7 dias (ou não há mensagem registrada).

Qualificado é um marcador do funil (passou pela etapa 2), não um estado terminal: um lead pode ser "qualificado" e ainda assim estar em "pendente" ou "perdido".

### Saída do relatório

Duas partes no corpo do e-mail:

1. **Funil do período** (contagens do cohort e a queda entre etapas):
   ```
   Interessados     40
   Qualificados     22   (-18 no cadastro)
   Agendados        12   (-10 entre ver horário e pagar)

   Conversão: 12 de 40 (30%)
   ```
2. **Situação de quem não converteu:**
   ```
   Pendentes (quentes, ativos ≤7 dias):  16
   Perdidos (frios, >7 dias):            12
   ```
   Opcional: listar os pendentes (nome + telefone + etapa alcançada) para a clínica agir; os perdidos ficam só como número. Decidir na revisão se lista os pendentes.

## Componentes

- **Novo** `app/funnel_report.py`: lógica pura — recebe as linhas de eventos + últimas atividades + agora, e devolve as contagens do funil e as listas pendente/perdido. Sem I/O.
- **Novo** `scripts/send_funnel_report.py`: cron — lê `events` e `messages` da janela, chama a lógica pura, monta o corpo e envia via `app/email_sender.send_clinic_notification_email` (com o destinatário do funil). Registra nada de volta (read-only; não precisa de marcador de idempotência porque é 1 envio por período).
- **Novo** `.github/workflows/funnel_report.yml`: agenda semanal.
- **Testes:** `tests/test_funnel_report.py` (lógica pura), `tests/test_funnel_report_cron.py` (cron com Supabase e e-mail mockados).

## Salvaguardas

- Read-only: não escreve em `events`, `appointments` nem no Chatwoot. Zero risco para a operação.
- Se faltar variável de e-mail (SMTP/destinatário), falha explícita sem enviar, igual aos outros relatórios.
- Janela limitada (30 dias) mantém a query barata.
- Não usa marcador de "já enviado": como é um resumo do período inteiro, reenviar é inofensivo (no máximo dois e-mails iguais); a periodicidade é controlada pelo cron.

## Riscos e mitigações

- **Última atividade só por `messages`:** um lead que interage sem gerar mensagem registrada pode parecer inativo. Mitigado usando `messages` (a fonte real da conversa) em vez dos eventos; é a mesma fonte que o rastreio de lead já usa.
- **Cohort vs período:** contamos por cohort de chegada (quem chegou na janela), não por eventos soltos no período, para a conversão ser dos mesmos leads. Isso é mais fiel que contar eventos avulsos.
- **Telefone desnormalizado:** `events.phone` e `messages.phone` são dígitos crus; a junção é por esse dígito, consistente entre as duas tabelas.

## Trabalho fora de escopo (para depois)

- Dashboard visual do funil.
- Recorte por médico ou por origem do lead.
- Métrica de tempo médio entre etapas.
