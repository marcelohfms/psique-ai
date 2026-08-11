# Desfechos de consulta: Não compareceu e Alta

**Data:** 2026-08-11
**Status:** aprovado para planejamento

## Problema

Hoje toda consulta passada é marcada como `completed` pelo cron
`scripts/complete_appointments.py` (~24h após o fim). Não existe forma de
registrar que o paciente **faltou**, nem de dar **alta** (encerrar o
tratamento e parar os lembretes de retorno). Consequências:

- Quem faltou fica indistinguível de quem compareceu (`completed`),
  contaminando relatório, cobrança e reativação.
- Não há onde a clínica registrar a falta — nem planilha nem dashboard
  servem bem para isso hoje.
- Um paciente que recebeu alta continua recebendo lembretes de retorno,
  porque a única saída da fila de classificação é escolher um intervalo de
  retorno.

## Superfícies escolhidas

Reaproveitamos telas onde a clínica já trabalha — nenhuma tela nova, nenhum
parsing de convenção:

- **Alta** vive só no `/retornos` do médico (`dashboard/main.py:277`,
  template `dashboard/templates/retornos.html`, lógica em
  `dashboard/return_reminders.py`): a fila onde o médico já pega cada
  consulta concluída e escolhe um intervalo de retorno
  (`get_pending_classification`). É ele quem sabe se o paciente merece alta.
- **Não compareceu** aparece em **duas** superfícies (mesma ação por baixo):
  o `/retornos` do médico e o painel de pagamentos da atendente. Detalhes na
  próxima seção.

## Dois pontos de entrada para `no_show`

A marcação de "não compareceu" tem a **mesma ação por baixo**
(`appointments.status = 'no_show'`) exposta em duas superfícies:

1. **Médico — `/retornos`:** botão "Não compareceu" na fila de
   classificação (ao lado dos botões de intervalo e do de "Alta").
2. **Atendente — painel de pagamentos** (`/pagamentos` e o painel embutido
   no Chatwoot; ambos usam `dashboard/payments.py::compute_pendencias`):
   botão "Não compareceu" ao lado do botão de pagar, em cada pendência.
   Como `compute_pendencias` só lista `status` em `["scheduled",
   "completed"]` (`payments.py:390`), marcar `no_show` **faz a pendência
   sumir do painel na hora** — a atendente vê a opção de primeira, sem
   procurar em abas. O botão age no **appointment inteiro**: se houver duas
   linhas para o mesmo appointment (taxa + consulta, ou 1ª consulta
   dividida), todas somem juntas.

## Os três desfechos

Para cada consulta pendente na fila, o médico agora tem três saídas:

| Desfecho | `appointments.status` | Linha em `return_reminders` | `next_return_date` | Sai da fila | Lembretes de retorno |
|---|---|---|---|---|---|
| **Intervalo** (15d…6m) | `completed` | sim | definido | sim | disparam no ciclo |
| **Não compareceu** | `no_show` | **não** | — | sim (via status) | nunca |
| **Alta** | `completed` | sim (`return_interval="alta"`) | `NULL` | sim (via `last_classified_appointment_id`) | nunca |

### Não compareceu

- **Registro durável:** seta `appointments.status = 'no_show'` no
  `appointment_id`. Esse é o novo estado de primeira classe. Não precisa de
  linha em `return_reminders`: a fila (`get_pending_classification`) busca
  consultas `completed`/passadas, então mudar o status para `no_show` já
  remove a consulta da fila, e sem linha em `return_reminders` não existe
  lembrete a disparar.
- **Efeito de graça:** `complete_appointments.py` só processa
  `status='scheduled'`, então uma consulta `no_show` não vira `completed`
  nem recebe o "pós-consulta" ("esperamos que a consulta tenha sido boa,
  agende a próxima").
- **Taxa de reserva — retida por default (passivo):** a taxa já paga
  simplesmente continua paga = retida. Não há reembolso automático, logo
  não há nada a "fazer" para reter. Coerente com a política: quem cai no
  fluxo de falta é quem não avisou com >24h de antecedência; se tivesse
  avisado, teria virado cancelamento (elegível a reembolso), não falta.
- **Override humano:** quando a atendente julgar situação fora do comum que
  mereça delicadeza, ela usa o **fluxo de reembolso da atendente que já
  existe** (`register_refund_request` em `app/graph/tools.py:3341` e o
  caminho de cancelamento em `tools.py:1508`). Nada novo a construir.
- **Sinalização (atendente):** **silenciosa**. Marcar `no_show` não gera
  aviso/flag para a atendente. A atendente só age se o próprio paciente
  procurar.
- **Mensagem ao paciente (parte 1 — acolher e convidar):** WhatsApp
  acolhedor convidando a remarcar, **sem** mencionar taxa. Ex.: *"Olá!
  Notamos que [nome] não conseguiu comparecer à consulta de [data]. Se
  quiser remarcar, é só responder por aqui que a gente te ajuda."*
  - **Gatilho:** **cron diário** (novo script, ou passo no cron existente),
    independente de onde/quando o `no_show` foi marcado. Não olha "consulta
    de ontem" — busca `appointments` com `status='no_show'` e
    `no_show_message_sent_at IS NULL`, envia, e marca a flag. Isso porque o
    no-show pode ser marcado dias depois da consulta (classificação
    atrasada); assim nenhum caso é perdido nem enviado em dobro.
  - **Nova coluna:** `appointments.no_show_message_sent_at` (flag de envio,
    espelhando `pos_consulta_sent_at`). É controle de envio, não
    classificação.
  - **Destinatários:** o(s) contato(s) com papel `consulta` do paciente
    (mesmo critério do pós-consulta).
- **Aviso de retenção da taxa (parte 2 — só quando o paciente topar
  remarcar):** conversacional, no bot. Quando o paciente `no_show` responde
  que quer remarcar, o bot explica que a taxa da consulta anterior foi
  retida por conta da falta e que há **nova taxa de reserva de R$ 100,00**
  para a nova data — reusando o padrão de mensagem "taxa recolhida + nova
  taxa" que já existe para remarcações fora do prazo
  (`app/graph/tools.py:1508-1525`). Como a consulta antiga fica `no_show`
  (não `scheduled`), remarcar já é uma reserva nova que cobra taxa nova; a
  parte 2 é garantir que o bot **reconheça a falta recente** e explique
  isso, em vez de tratar como remarcação gratuita. Implementação provável:
  instrução no prompt + helper que detecta `no_show` recente do paciente.

### Alta

- **Registro:** grava/atualiza linha em `return_reminders` com
  `return_interval = "alta"` (sentinela), `next_return_date = NULL`, e
  `last_classified_appointment_id = <appointment_id>`. O
  `last_classified_appointment_id` é o que tira a consulta da fila do
  médico (mesmo mecanismo dos intervalos normais). A consulta mantém
  `status='completed'`.
- **Lembretes desligados:** `pending_template` em
  `scripts/send_return_reminders.py` ganha um guarda para
  `return_interval == "alta"` que retorna `None` **antes** de tocar em
  `next_return_date` (mesmo padrão já usado para `"15_dias"`), evitando o
  `date.fromisoformat(None)` e garantindo que nenhum template dispare.
- **Não é permanente:** se o paciente de alta voltar para uma nova
  consulta, essa nova consulta concluída reaparece na fila do `/retornos`
  (porque `last_classified_appointment_id` != novo `appointment_id`) e o
  médico reclassifica. A alta vale para o ciclo daquela consulta.
- **Pós-consulta:** no passo de pós-consulta do `complete_appointments.py`,
  se a consulta já foi classificada como alta (linha `return_reminders` com
  `return_interval="alta"` e `last_classified_appointment_id` == esta
  consulta), o cron **pula** o "agende a próxima". Isso só ajuda na janela
  em que a alta é marcada **antes** de o cron enviar (possível porque a fila
  já mostra consultas de ontem ainda `scheduled`). Se a alta for marcada
  depois do envio, a mensagem já saiu e não há como retratar — comportamento
  aceito.

## Mudanças por camada

### Banco (`supabase/migrations/`)
- `appointments.status` passa a aceitar `'no_show'` (se houver `CHECK`/enum
  na coluna, incluir `no_show`; verificar na migration existente).
- `return_reminders.return_interval` passa a aceitar o sentinela `'alta'`
  (idem, se houver constraint).
- **Nova coluna** `appointments.no_show_message_sent_at timestamptz` (flag
  de envio da mensagem de falta, espelhando `pos_consulta_sent_at`).

### `dashboard/return_reminders.py`
- Nova função para gravar **alta**: escreve linha `return_reminders` com
  `return_interval="alta"`, `next_return_date=NULL`,
  `last_classified_appointment_id=<appt>` (upsert 1 linha por paciente, como
  `save_classification`). Não passa pela validação de `RETURN_INTERVALS`.
- Nova função (ou reuso) para gravar **no_show**: seta
  `appointments.status='no_show'` no `appointment_id`.
- `get_pending_classification`: garantir que consultas `no_show` fiquem fora
  da fila (o filtro atual por `completed` já exclui; confirmar que qualquer
  inclusão de consultas passadas ainda `scheduled` também exclui `no_show`).

### `dashboard/payments.py` + painel de pagamentos
- Função para marcar `no_show` a partir de um `appointment_id` (reuso da
  função de no_show acima). `compute_pendencias` não muda — o filtro
  `status in ["scheduled","completed"]` já exclui `no_show`.
- Template do painel de pagamentos (`dashboard/templates/pagamentos.html` e
  o painel embutido no Chatwoot): botão **"Não compareceu"** por pendência,
  ao lado do botão de pagar.

### `dashboard/main.py`
- `RetornoBody` / novos endpoints (ou um campo de desfecho no endpoint
  existente `POST /api/retornos/{patient_id}`) para "não compareceu" e
  "alta", chamando as funções acima.
- Endpoint para marcar `no_show` a partir do painel de pagamentos.
- Nenhum endpoint dispara a mensagem de falta — isso é responsabilidade do
  cron diário (ver abaixo).

### `scripts/` (cron da mensagem de falta)
- Script (novo ou passo em cron existente): busca `appointments` com
  `status='no_show'` e `no_show_message_sent_at IS NULL`, envia a mensagem
  de falta (parte 1) para os contatos com papel `consulta`, e marca
  `no_show_message_sent_at`. Espelha a estrutura de
  `scripts/complete_appointments.py`. Agendar no GitHub Actions.

### `app/graph/` (parte 2 — aviso de retenção no bot)
- Detectar `no_show` recente do paciente e, na intenção de remarcar,
  explicar a retenção da taxa anterior + nova taxa de R$ 100,00 — reusando
  o padrão de `tools.py:1508-1525`. Provável: helper de lookup + instrução
  no prompt (`app/graph/prompts.py`).

### `dashboard/templates/retornos.html`
- Dois botões novos por item da fila: **"Não compareceu"** e **"Alta"**,
  ao lado dos botões de intervalo.

### `scripts/send_return_reminders.py`
- Guarda em `pending_template` para `return_interval == "alta"` → `None`.
- A query já filtra `.neq("return_interval", "15_dias")`; opcionalmente
  também excluir `"alta"` (não obrigatório, pois `pending_template` já
  retorna `None`, mas evita carregar linhas inúteis).

### `scripts/complete_appointments.py`
- No passo de pós-consulta (`_process_pos_consulta`): pular o envio se a
  consulta já foi classificada como alta.

## Testes (obrigatório por CLAUDE.md)
- `dashboard/tests/`: gravar alta (sentinela, sem `next_return_date`);
  gravar no_show (status muda); ambos saem de `get_pending_classification`;
  alta reaparece na fila numa consulta posterior.
- `scripts` (`send_return_reminders`): linha `alta` nunca vira candidato,
  mesmo com `next_return_date` nulo (sem crash).
- `scripts` (`complete_appointments`): consulta com alta não recebe
  pós-consulta; consulta `no_show` não é marcada `completed` nem recebe
  pós-consulta.
- `dashboard`: marcar `no_show` pelo painel de pagamentos remove a pendência
  de `compute_pendencias` (consulta `no_show` não aparece); botão em ambas
  as superfícies (retornos e pagamentos) chama a mesma função.
- `scripts` (cron da mensagem de falta): envia só para `no_show` com
  `no_show_message_sent_at` nulo; marca a flag; não reenvia (envio mockado).
- `app/graph` (`test_tools`/`test_process_message`): paciente com `no_show`
  recente que pede remarcação recebe o aviso de taxa retida + nova taxa, e
  **não** o benefício de remarcação gratuita.

## Fora de escopo
- Retração de pós-consulta já enviado (impossível).
- Qualquer automação de reembolso no no_show (é sempre manual, via atendente).
- Política de crédito/reaproveitamento da taxa retida além do reembolso
  manual existente.
