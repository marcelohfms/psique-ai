# Lembretes de retorno periódico

Data: 2026-07-13

## Contexto

A clínica quer lembrar pacientes crônicos (em uso de medicação controlada) de
agendar o retorno periódico com o médico, antes que fiquem sem receita válida
(Art. 37 do Código de Ética Médica). Hoje não existe nenhum conceito de
"cadência de retorno" no sistema — `patients.is_returning_patient` só marca se
o paciente já teve alguma consulta, não quando ele deve voltar.

O pedido veio com 3 templates de WhatsApp já redigidos (1 mês antes, no mês do
retorno, atrasado) e a necessidade de guardar, por paciente, de quanto em
quanto tempo ele deve retornar.

## Modelo de dados

Nova tabela `return_reminders`, **separada de `patients`** (decisão do
usuário: `patients` deve continuar focada em dados do paciente em si; tudo
relacionado ao mecanismo de lembrete de retorno fica isolado):

```sql
CREATE TABLE return_reminders (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                      UUID NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id                       UUID NOT NULL REFERENCES doctors(doctor_id),
    return_interval                 TEXT NOT NULL CHECK (return_interval IN ('15_dias','1_mes','3_meses','6_meses')),
    next_return_date                DATE NOT NULL,
    last_classified_appointment_id  UUID REFERENCES appointments(appointment_id),
    month_before_sent_at            TIMESTAMPTZ,
    month_of_sent_at                TIMESTAMPTZ,
    overdue_sent_at                 TIMESTAMPTZ,
    updated_at                      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_return_reminders_patient ON return_reminders(patient_id);
```

- Uma linha por paciente (não por ciclo). Cada nova classificação sobrescreve
  `return_interval`, `next_return_date`, `last_classified_appointment_id` e
  zera as 3 flags de envio — começa um novo ciclo de lembretes.
- `last_classified_appointment_id` registra qual consulta gerou o
  `next_return_date` atual. É o que permite ao dashboard saber se uma consulta
  concluída mais recente ainda não foi classificada (ciclo pendente).
- Só existe linha para pacientes que o médico já classificou pelo menos uma
  vez. Sem linha = sem rastreio, sem lembrete.

### Caso especial: `15_dias`

Um retorno de 15 dias não cabe na lógica de lembrete mensal (não faz sentido
mandar "1 mês antes" ou "atrasado" para um intervalo tão curto) — mas o
médico ainda precisa poder registrar essa decisão clínica. Ao salvar
`return_interval = '15_dias'`, o dashboard já grava as 3 colunas de envio como
preenchidas (`now()`) no mesmo momento, deixando claro que não há lembrete
pendente. O cron também pula explicitamente linhas `15_dias` como camada
redundante de segurança.

### Caso especial: `1_mes` pula "um mês antes"

Para `1_mes`, `next_return_date` é sempre exatamente "mês da consulta + 1" —
ou seja, a condição de `retorno_um_mes_antes` (mês atual == mês-alvo − 1) já
fica verdadeira no dia seguinte à própria classificação, disparando um
lembrete "um mês antes" que na prática sai colado na consulta que acabou de
acontecer (sem sentido temporal, mesmo problema do `15_dias`). Por isso,
`1_mes` também tem `month_before_sent_at` já gravado (`now()`) no momento da
classificação — só dispara `retorno_no_mes` e, se ainda não agendou,
`retorno_atrasado` (no máximo 2 lembretes).

## Dashboard (`/retornos`)

Nova página em `dashboard/`, HTTP Basic auth (mesmo padrão de
`pagamentos.html`/`verify_credentials`), com abas **Dr. Júlio** / **Dra.
Bruna** (é uma única credencial compartilhada da equipe — a separação por
médico é só um filtro na UI, não login separado).

**Seção "Hoje":** pacientes com consulta hoje para o médico selecionado.
Sempre visível, permite classificar (ou reclassificar) na hora, mesmo com a
consulta ainda em andamento — não depende do status virar `completed` (isso
só acontece ~24h depois, via `scripts/complete_appointments.py`).

**Seção "Pendentes de classificação":** pacientes com consulta concluída
(`status = 'completed'`) cuja consulta mais recente ainda não foi classificada
(`return_reminders` inexistente, ou `last_classified_appointment_id` diferente
do id dessa consulta), ordenados da mais antiga para a mais nova — funciona
como fila de pendências que vai esvaziando.

Cada linha: nome do paciente, data da consulta, dropdown de intervalo (Nenhum
/ 15 dias / 1 mês / 3 meses / 6 meses — pré-selecionado com o valor salvo
anteriormente, se houver) + botão "Salvar".

Ao salvar (`POST /api/retornos/{patient_id}`, mesmo padrão de auth dos outros
endpoints do dashboard): upsert em `return_reminders` com
`next_return_date = data_da_consulta + intervalo`, atualiza
`last_classified_appointment_id`, e zera as flags de envio — exceto nos casos
especiais: `15_dias` já marca as 3 como enviadas; `1_mes` já marca
`month_before_sent_at` como enviada (ver seção "Cron de envio" abaixo).

## Cron de envio (`scripts/send_return_reminders.py`)

Roda 1x/dia via GitHub Actions (`0 11 * * *`, ~8h Recife) — cadência diária
(não mensal): a lógica de janela já é por mês calendário, então rodar todo dia
garante que o primeiro envio de cada ciclo aconteça assim que o mês vira, e
que qualquer envio que não coube no lote do dia 1 (por causa do throttling)
seja retomado automaticamente nos dias seguintes, já que a flag só é marcada
em caso de sucesso — sem precisar de lógica extra de retry.

Para cada linha de `return_reminders` (`15_dias` sempre pulada — as 3 flags já
vêm marcadas; `1_mes` só concorre a `retorno_no_mes`/`retorno_atrasado`, já
que `month_before_sent_at` também vem pré-marcado nesse caso):

1. **Pula** se o paciente já tem consulta futura agendada **com o mesmo
   médico** (`return_reminders.doctor_id`) — evita insistir com quem já
   remarcou.
2. Compara o mês atual com o mês de `next_return_date` usando **(ano, mês)
   como par, nunca só o número do mês** — comparar só `mês` quebra na virada
   de ano (ex: dezembro/2026 vs janeiro/2027 não pode ser tratado como
   "dezembro == janeiro − 1" sem considerar o ano). Normalizar como
   `ano*12 + mês` antes de comparar:
   - `chave_atual == chave(next_return_date) − 1`, e `month_before_sent_at`
     nulo → candidato a `retorno_um_mes_antes`.
   - `chave_atual == chave(next_return_date)`, e `month_of_sent_at` nulo →
     candidato a `retorno_no_mes`.
   - `chave_atual > chave(next_return_date)`, e `overdue_sent_at` nulo →
     candidato a `retorno_atrasado` (envio único, sem repetição mensal).
3. Todos os candidatos do dia são enviados em **lotes de 10, com pausa de 60s
   entre lotes** (throttling para reduzir risco de flag de spam pela Meta).
   Cada envio: para todos os contatos com role `consulta` do paciente (mesmo
   padrão de `send_appointment_reminders.py`), via `send_template_message`,
   com fallback de conteúdo plano salvo no checkpoint do LangGraph (mesmo
   padrão de `save_to_checkpoint` nos outros scripts de lembrete).
4. A flag correspondente só é marcada após pelo menos um envio bem-sucedido
   por paciente (mesmo padrão de `sent_col` nos outros scripts).

**Consequência aceita:** como o gatilho é por mês calendário e não por
contagem exata de dias, pacientes com `1_mes` podem, em casos de borda,
receber "um mês antes" e "no mês" em sequência próxima. Foi uma escolha
deliberada (mais simples e mais fiel ao texto original dos templates) em vez
de janelas por dias exatos.

## Templates WhatsApp (Meta)

3 templates novos, categoria **UTILITY**, idioma `pt_BR`, variáveis do corpo
`{{1}}` = primeiro nome do contato, `{{2}}` = médico(a) (ex: "Dr. Júlio"):

- `retorno_um_mes_antes`
- `retorno_no_mes`
- `retorno_atrasado`

Corpo = texto fornecido pelo usuário (ver mensagem original). Precisam ser
criados e aprovados no Meta Business Manager antes do cron poder enviá-los —
mesmo pré-requisito dos templates existentes. Serão documentados em
`docs/whatsapp-templates.md` seguindo o padrão já usado ali.

## Testes

- `tests/test_return_reminders.py`: cálculo de `next_return_date` por
  intervalo, seleção do template certo por mês calendário (usando chave
  ano*12+mês, incluindo virada de ano), skip de `15_dias`, skip de
  `retorno_um_mes_antes` para `1_mes`, skip quando há consulta futura
  agendada com o mesmo médico, garantia de envio único por flag,
  batching/throttling.
- `dashboard/tests/test_retornos_routes.py` (novo módulo): endpoint
  `POST /api/retornos/{patient_id}` — upsert correto, cálculo de
  `next_return_date`, caso especial `15_dias` marcando as 3 flags como
  enviadas, caso especial `1_mes` marcando `month_before_sent_at` como
  enviada, filtros das seções "Hoje" / "Pendentes" por médico.
