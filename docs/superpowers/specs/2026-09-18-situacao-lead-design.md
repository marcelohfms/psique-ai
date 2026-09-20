# Situação do lead: rastreio de abandono no cadastro + labels de situação no Chatwoot

Data: 2026-09-18
Status: aprovado (design), aguardando plano de implementação.
Origem: `AUDITORIA-ESTRATEGICA-2026-09.md`, seções 5.2, 7.1 e 8.

## Contexto e problema

Hoje o lead que abandona a conversa **no meio do cadastro**, antes de ver horários, fica invisível. O rastreio de abandono só começa no evento `slots_offered` ([app/scheduling_stall.py:25](../../../app/scheduling_stall.py)), então quem para durante a coleta de dados não recebe cutucada nem entra em nenhum relatório. É o momento de maior intenção e o mais fácil de recuperar.

Além disso, a situação do lead vive só na tabela `events`. A atendente não consegue ver, olhando o Chatwoot, quem acabou de chegar, quem abandonou o cadastro ou quem abandonou o agendamento. O backend já sabe todos esses estados, mas nada vira label na conversa.

## Objetivo

Duas entregas juntas, ambas aditivas e fora do caminho quente do webhook:

1. Detectar o abandono de cadastro e cutucar o lead (nudge), espelhando o padrão já testado do `scheduling_stall`.
2. Refletir a situação do lead como label no Chatwoot, para a atendente ver e filtrar.

## Não-objetivos

- Não mexer no handoff nem no gate de pausa do bot (decisão explícita: não desestabilizar a operação).
- Não tocar o webhook nem o grafo. Toda a lógica nova roda na camada de cron.
- Não criar views/filtros no Chatwoot neste momento (as labels são o pré-requisito; as views a clínica cria depois no painel).
- Não enviar nudge para quem nunca deu o nome (lead sem intenção real).

## Decisões tomadas no brainstorming

- Ação no abandono de cadastro: **nudge + label + relatório** (espelha o `scheduling_stall`).
- Corte mínimo para nudge: **o lead deu pelo menos o nome**.
- Janela de silêncio antes de cutucar: **4 horas** (consistente com o `scheduling_stall` e quase sempre dentro da janela de 24h do WhatsApp).
- Labels desta entrega: **`lead-novo`**, **`cadastro-abandonado`**, **`agendamento-abandonado`**.
- Abordagem: **A**, tudo na camada de cron, nada no fluxo ao vivo.

## Arquitetura

### Componentes

- **`app/lead_stall.py`** (novo): lógica pura e testável. Não faz I/O. Recebe os fatos de um lead e devolve a situação. Reusa `app/scheduling_stall.py` para a parte de agendamento, para não duplicar regra.
- **`scripts/send_lead_stall_nudges.py`** (novo): o cron. Faz o I/O (Supabase, Chatwoot, WhatsApp), orquestra detecção, reconciliação de label, nudge e marcação.
- **`.github/workflows/lead_stall_nudges.yml`** (novo): agenda o cron.
- **`scripts/send_scheduling_stall_report.py`** (editar): adicionar uma seção de `cadastro-abandonado` frio no e-mail diário de abandonos que a clínica já recebe, em vez de criar um novo e-mail.

### Por que na camada de cron

O caminho que atende o paciente ao vivo (webhook + grafo) não é tocado. É o menor risco possível para um sistema em produção. A única concessão é a label aparecer com até 30 minutos de atraso, o que é irrelevante para uma visão de atendente.

## Modelo de detecção

### Fatos coletados por lead (tudo de tabelas já existentes)

- Última mensagem do paciente e seu horário: tabela `messages` (role `user`).
- Início da conversa: evento `conversation_started` em `events`.
- Cadastro completo ou não: reusar a lógica existente `missing_registration_field` ([app/database.py:340](../../../app/database.py)), carregando o lead via `get_user_by_phone` (o shim reconstrói de `patients`/`contacts`).
- Tem nome: campo `patient_name`/`name` do lead, não vazio.
- Tem agendamento: tabela `appointments` (qualquer consulta ativa/futura).
- Viu horários: evento `slots_offered` sem `appointment_booked`/`appointment_rescheduled` depois (reusar `scheduling_stall.select_abandoned`).
- Está pausado: `contacts.active is False` ou `manual_hold` (pular).

### Classificação da situação (função pura em `app/lead_stall.py`)

As três labels são mutuamente exclusivas. Cada lead tem no máximo uma. A situação é recalculada a cada rodada.

- **`agendamento-abandonado`**: viu horários e não confirmou em 4h ou mais, sem agendamento depois. (Precede as demais: se viu horários, o cadastro já estava completo.)
- **`cadastro-abandonado`**: cadastro incompleto, deu pelo menos o nome, silêncio de 4h ou mais, contato ativo, sem agendamento.
- **`lead-novo`**: começou a conversa, cadastro incompleto, contato ativo, sem agendamento, e última mensagem há menos de 4h.
- **nenhuma**: cadastro completo (e sem abandono de agendamento), ou lead que sumiu sem nunca ter dado o nome, ou contato pausado.

### Transições (reconciliação a cada rodada)

A cada rodada o cron calcula a situação atual e, se ela mudou desde a última vez, ajusta a label no Chatwoot: adiciona a label da situação atual e remove as outras duas labels de lead.

- Cadastro completa: remove qualquer label de lead.
- Agenda: remove qualquer label de lead.
- `lead-novo` que passa de 4h de silêncio com nome: vira `cadastro-abandonado`.
- `lead-novo` que passa de 4h de silêncio sem nome: perde o `lead-novo`, não vira abandonado.

## Ações

### Label

Aplicada dentro do cron, em lote. Convenção de nome: português, minúsculo com hífen, sem o prefixo `eva-` (esse é só para controle do bot). As labels de controle `eva-ativa`/`eva-inativa` nunca são tocadas por este fluxo.

### Nudge (só `cadastro-abandonado`)

Espelha o `scheduling_stall`:

- Só quando o contato está ativo e dentro da janela de 24h do WhatsApp. Fora disso, não manda; o lead entra no relatório.
- Só entre 8h e 20h de Recife.
- Uma cutucada por lead (marcada em `events`).
- Espaçamento entre envios para não estourar o rate limit, igual aos crons de lembrete.

Texto proposto (tom da Eva, curto, personaliza com o nome quando houver):

> Oi, {nome}! 😊 Vi que a gente começou seu cadastro aqui na Clínica Psique mas não chegou a finalizar. Quer continuar de onde a gente parou? É rapidinho, é só me responder por aqui.

Sem nome disponível, cai para "Oi!" no lugar de "Oi, {nome}!". Após o nudge, quando o lead responde, o fluxo normal segue (ele continua em `collect_info`), sem tratamento especial.

### Relatório

Seção nova no e-mail diário de abandonos (`send_scheduling_stall_report.py`), listando os `cadastro-abandonado` que não foram cutucados (frios ou fora da janela), para a atendente decidir se aborda. Não cria e-mail novo.

## Salvaguardas

- **Idempotência sem estado no processo**: o cron do GitHub Actions nasce sem memória a cada rodada. Registrar em `events` a última label setada por lead e a marca de nudge enviado. Em regime permanente, uma rodada não faz nenhuma chamada de escrita ao Chatwoot; só escreve quando a situação muda. Protege do rate limit da API.
- **Limite de idade**: só considerar leads com atividade nos últimos 7 dias. No primeiro deploy, não dispara nudge nem relatório para abandono antigo.
- **Respeito à pausa e à cortesia**: contato pausado (`eva-inativa`, `active=false` ou `manual_hold`) é pulado por completo (sem nudge, sem mexer em label).
- **Sem sobreposição com o `scheduling_stall`**: o novo cron só faz **nudge** para `cadastro-abandonado`. Para `agendamento-abandonado` ele só aplica a **label**; a cutucada de agendamento continua sendo feita pelo `scheduling_stall` existente, sem duplicar.
- **Janela do WhatsApp**: nudge livre só dentro de 24h desde a última mensagem do paciente; fora disso, vira relatório.

## Novos eventos em `events`

- `lead_label_set` (metadata: label atual, conversation_id) para a reconciliação idempotente saber a última label setada.
- `lead_stall_nudge_sent` para garantir uma cutucada por lead.

## Testes

Seguindo o CLAUDE.md (um arquivo por camada):

- `tests/test_lead_stall.py`: lógica pura de classificação e seleção (sem I/O), incluindo os limites de 4h, corte por nome, mutualidade das labels e limite de 7 dias.
- `tests/test_lead_stall_crons.py`: o cron com Supabase, Chatwoot e WhatsApp mockados, no padrão de `tests/test_scheduling_stall_crons.py`. Cobrir: nudge dentro/fora da janela, pulo de pausado/cortesia, uma-cutucada-só, reconciliação de label só quando muda, e a seção do relatório.

Todos os testes devem passar no CI (`.github/workflows/test.yml`).

## Riscos e mitigações

- **Volume de chamadas ao Chatwoot**: mitigado pela reconciliação só-quando-muda e pelo limite de 7 dias.
- **Spam / flag da Meta**: mitigado pelo corte por nome, uma cutucada só, janela 8h-20h e limite de idade.
- **Divergência de regra com o `scheduling_stall`**: mitigado reusando `scheduling_stall.select_abandoned` para a parte de agendamento.

## Trabalho fora de escopo (registrado para depois)

- Views/filtros no Chatwoot por essas labels (a clínica cria no painel depois).
- Custom attributes na conversa (médico, próxima consulta, taxa paga), que é a outra frente da auditoria.
- Migração do handoff para o status nativo do Chatwoot.
