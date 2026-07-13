# Auditoria completa — Psique Chatbot

Data: 2026-07-13
Escopo: `app/`, `dashboard/`, `supabase/migrations/`, `.github/workflows/`, `scripts/`, `tests/`, Dockerfiles.
Metodologia: 6 investigações paralelas (uma por área) + verificação manual direta no código dos achados mais críticos antes de entrarem neste relatório. Onde a verificação manual invalidou um achado, ele foi removido ou rebaixado — isso está anotado explicitamente. Achados marcados **[não re-verificado]** vieram só da investigação automatizada e merecem confirmação antes de virar código.

**Nenhuma correção foi aplicada. Este documento é só diagnóstico.**

---

## Como ler isto

- 🔴 **Crítico** — pode causar perda de agendamento, exposição de dados de paciente, ou queda total do bot.
- 🟠 **Alto** — causa comportamento incorreto real, mas com escopo mais limitado ou exige uma condição específica.
- 🟡 **Médio** — problema real, mas de baixo impacto direto ou de exploração improvável.
- ⚪ **Baixo** — melhoria de robustez/observabilidade, sem incidente concreto associado.

---

## 0. Tabela `users` depreciada (grupo próprio, conforme solicitado)

**Situação real:** a tabela `users` **ainda existe e ainda é usada em produção** — não há nenhuma migration que a derrube ou renomeie. A migration `supabase/migrations/20260615_create_patients_contacts.sql` documenta explicitamente que as tabelas novas convivem com `users` até o backfill e "corte do shim".

### 🔴 Código de produção com acesso direto à tabela antiga (fora do shim)

| Arquivo:linha | Operação | Trecho | Tabela que deveria substituir |
|---|---|---|---|
| [app/main.py:665](app/main.py:665) | **WRITE (DELETE)** | `client.from_("users").delete().eq("number", stripped).execute()` | Deveria apagar de `contacts` + `patients` (via `patient_contacts`), não de `users`. Faz parte do endpoint admin de reset de conversa. |
| [dashboard/main.py:187](dashboard/main.py:187) | **READ (SELECT)** | `.from_("users").select("number, name").in_("number", phones)` | `contacts.select("phone, name")` — usado para popular nomes na lista de conversas do painel do atendente, roda a cada carregamento. |

Ambos verificados diretamente (grep confirmou as linhas exatas). São os únicos dois pontos de código ativo (não-shim, não-script) que ainda tocam `users` diretamente.

### ✅ Shims seguros (uso intencional, não precisam de correção agora)
- [app/database.py:103](app/database.py:103) `get_users_by_phone()` — reconstrói formato antigo a partir de `patients`+`contacts`.
- [app/database.py:141](app/database.py:141) `get_user_by_phone()` — wrapper do acima.
- [app/database.py:159](app/database.py:159) `upsert_user()` — escreve roteando para `patients`/`contacts`.
- Todos os chamadores em `app/graph/tools.py` e `app/main.py` passam por esses shims — seguros.

### 🟡 Fallbacks defensivos (degradação segura, mas revelam que `users` ainda é join válido)
- `scripts/send_appointment_reminders.py:102` — `appt.get("patients") or appt.get("users") or {}`
- `scripts/release_pending_reschedules.py:100` — `appt.get("users") or {}`

### ⚪ Scripts one-off (baixo risco, fora do caminho crítico)
Dezenas de `scripts/_check_*.py`, `_fix_*.py` fazem SELECT/UPDATE direto em `users` para diagnóstico manual. Não rodam automaticamente. Ver seção de segurança abaixo — alguns desses scripts têm um problema mais sério (dados reais de paciente hardcoded).

### Menção em prompts do LLM
Verificado: **nenhuma referência a `users`** em `app/graph/prompts.py` ou em qualquer string de system prompt. Não há menção nos workflows `.yml` tampouco.

### Migration de backfill já existe
`scripts/migrate_users_to_patients_contacts.py` já faz a cópia idempotente `users → patients+contacts`. `patients` tem coluna `legacy_user_id` para rastrear a migração.

---

## 1. Supabase em geral

### 🔴 Ausência total de RLS (Row Level Security)
**Verificado:** nenhum arquivo em `supabase/migrations/*.sql` contém `ENABLE ROW LEVEL SECURITY` ou `CREATE POLICY` (grep vazio).
- **Impacto real:** qualquer chave Supabase vazada (anon ou service_role) dá acesso de leitura/escrita irrestrito a `patients`, `contacts`, `appointments`, `messages` — nome completo, CPF, telefone, motivo da consulta, médico, status de pagamento de todos os pacientes.
- **Correção:** habilitar RLS nas tabelas com dado de paciente e criar políticas (leitura restrita por `service_role` no backend; se o dashboard algum dia expuser acesso direto do cliente, políticas por sessão/atendente).

### 🟠 Uma única env var para a chave Supabase, sem diferenciar anon/service_role
**Verificado:** [app/database.py:23](app/database.py:23) e [dashboard/db_client.py:17](dashboard/db_client.py:17) leem `os.environ["SUPABASE_KEY"]`. O próprio `.env.example:25` diz `SUPABASE_KEY=your_anon_or_service_role_key` — ambíguo por design.
- **Impacto:** não há como saber, só lendo o código, se a chave em uso em produção é `service_role` (que já ignora RLS mesmo que ela seja implementada) ou `anon`. Combinado com o item acima, esse é o ponto que precisa ser resolvido junto com RLS.
- **Correção:** variáveis separadas `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY`, com uso explícito conforme o contexto (backend interno = service_role; qualquer endpoint tocável externamente = anon + RLS).

### 🟠 Exceções engolidas silenciosamente em operações de escrita críticas
- [app/database.py:232-242](app/database.py:232) `log_event()` — `except Exception: pass` (ou equivalente).
- [app/database.py:376-386](app/database.py:376) `save_message()` — idem.
- **Impacto:** se o insert falhar (RLS, rede, constraint), o código segue como se tivesse dado certo. Mensagem não fica salva, evento de auditoria não é registrado, e ninguém percebe até um paciente reclamar de algo que "sumiu".
- **Correção:** logar a exceção com `logger.exception(...)` no mínimo; considerar alerta para falhas em `confirm_appointment`/`save_message`.

### 🟡 `IndexError` em acesso a `.data[0]` — **investigado e não confirmado**
O agente de pesquisa apontou 4 linhas (`patients.py:137`, `database.py:404`, `tools.py:1142-1143`, `tools.py:1463`) como acesso desprotegido a `.data[0]`. **Verifiquei as 4 manualmente: todas têm guard `if x.data:` antes do acesso.** Não é um bug real — removido da lista de correções. Mantenho o registro aqui só para não perder o rastro caso o padrão se repita em outro lugar não amostrado.

### ⚪ Doctor IDs hardcoded [não re-verificado]
`app/database.py:6-11` — UUIDs de médicos como constantes no código. Baixo risco operacional (clínica pequena, só 2 médicos), mas exige rebuild/deploy para adicionar um médico novo.

---

## 2. Google Calendar

### 🔴 Ordem "Calendar primeiro, Supabase depois" em `confirm_appointment` — **verificado**
[app/graph/tools.py:594-707](app/graph/tools.py:594)
- Linha 595: `create_event()` é chamado **primeiro**.
- Linhas 611-686: lógica de negócio roda **depois** do evento já existir no Calendar (resolução de paciente, cálculo de `consultation_type`, etc.) — inclui outra chamada Supabase própria.
- Linha 688: só então o insert em `appointments` acontece.
- Linha 701-706: se o insert falhar, tenta `cancel_event()` — mas esse próprio `except Exception: pass` (linha 705) engole falha do rollback também.
- **Gap adicional confirmado na leitura:** se `user` não for resolvido (paciente não encontrado por telefone), o insert acontece assim mesmo com `patient_id: None` (linha 689: `user["id"] if user else None`) — **não há falha**, cria-se um agendamento órfão sem paciente vinculado, silenciosamente.
- **Cenário real:** processo reinicia/crasha entre a linha 595 (evento criado) e a 700 (insert), ou o rollback na 704 falha silenciosamente — evento existe no Google Calendar, paciente acha que está agendado, mas não há registro em `appointments`. Ou: paciente novo cujo telefone ainda não bateu no cadastro gera um agendamento com `patient_id=NULL`.
- **Correção:** inverter a ordem (Supabase primeiro, com constraint única em `(patient_id, start_time, status='scheduled')`, depois Calendar); se `user` for `None`, falhar explicitamente em vez de inserir com `patient_id=NULL`; parar de engolir a exceção do rollback (linha 705) — logar com alerta se o rollback falhar, pois aí sobra um evento órfão no Calendar sem ninguém saber.

### 🟠 Race condition entre checagem de conflito e criação do evento [não re-verificado, mas plausível dado o padrão acima]
[app/graph/tools.py:524-562] (aproximado) — checagem de duplicidade/disponibilidade e a criação do evento não estão em uma transação; duas mensagens quase simultâneas (retry do WhatsApp, duplo clique) podem passar pelas duas checagens antes de qualquer uma criar o evento, resultando em dois agendamentos para o mesmo horário. Recomendo validar com um teste de concorrência antes de assumir a extensão exata do problema.

### 🟠 Sem retry/distinção de erro no refresh do token OAuth
[app/google_calendar.py:217-225] — `Credentials(token=None, refresh_token=..., ...)` força refresh a cada chamada; falhas de auth (token revogado) e falhas transitórias de rede são tratadas da mesma forma (`except Exception` genérico em vários pontos, ex. linhas ~385-394). Se o refresh token for revogado, todo booking passa a falhar até alguém notar e re-autorizar manualmentte — sem alerta automático.

### 🟡 Timezone: uso correto de `America/Recife`, mas com um ponto de risco [não re-verificado]
O código usa `zoneinfo.ZoneInfo("America/Recife")` consistentemente, mas há uma chamada `datetime.fromisoformat(...).astimezone(TZ)` que, se a string vier sem timezone (naive), assume o horário do sistema operacional do container em vez de Recife — só é um problema se a variável `TZ` do container/deploy não estiver setada corretamente. Vale conferir a config do Dockerfile/Easypanel.

### 🟡 Sem coluna dedicada para reconciliação
O ID do evento do Google Calendar é salvo em `appointments.appointment_id` (reaproveitando a mesma coluna, não um `google_event_id` separado) — funciona, mas dificulta auditoria/reconciliação caso o evento seja apagado manualmente no Google Calendar por um médico: não há registro de "eu sei que este evento sumiu e ninguém tratou isso".

---

## 3. Bugs e lógica de conversa

### 🟠 Estado `human_handoff` nunca é atribuído em lugar nenhum, mas existe no type — **verificado**
[app/graph/state.py:6](app/graph/state.py:6): `ConversationStage = Literal["collect_info", "patient_agent", "human_handoff"]`
[app/graph/graph.py:58-61](app/graph/graph.py:58): o `set_conditional_entry_point` só mapeia `"collect_info"` e `"patient_agent"` — `"human_handoff"` não está no `path_map`.

**Importante — corrigindo a severidade do achado original:** verifiquei com `grep` que **nenhum lugar no código hoje atribui `stage = "human_handoff"`** — não é um bug acontecendo agora, é uma armadilha latente. O nome sugere fortemente uma funcionalidade de handoff para atendente humano que foi projetada (está no type) mas nunca implementada (não há node, não há edge). Se alguém no futuro adicionar `stage="human_handoff"` em algum lugar (parece natural, dado o nome), o LangGraph vai falhar ao rotear — risco de regressão silenciosa em feature futura, não de incidente hoje.
- **Correção:** ou implementar o node `human_handoff` de fato, ou remover o valor do `Literal` até que exista suporte, para que o type-checker acuse qualquer tentativa de uso.

### ⚪ "Race condition" no buffer de mensagens — **investigado e não confirmado como crítico**
O agente de pesquisa reportou [app/buffer.py:51-68](app/buffer.py:51) como uma read-modify-write desprotegida. Na leitura direta: `_fire()` roda de forma síncrona dentro do event loop (sem `await` no meio), e `push()` também não tem nenhum `await` entre o append e o agendamento do `call_later` — em asyncio single-thread, isso significa que não há ponto de interleaving real entre o clear de `entry.messages` e um novo `push()`. Além disso:
- já existe dedup de texto idêntico na mesma janela (linha 54: `if text not in entry.messages`);
- já existe `get_phone_lock(phone)` (usado em [app/main.py:483](app/main.py:483) e [app/main.py:972](app/main.py:972)) serializando as execuções do grafo por telefone.

Rebaixo esse achado — não vejo evidência de corrupção de estado por essa via específica hoje. Isso é diferente do bug histórico "Attendant Note Stage Race" (memória do projeto), que era uma race real no checkpoint do LangGraph, não neste buffer.

### 🟡 Duas tool calls conflitantes no mesmo turno [não re-verificado]
`confirm_appointment` e `mark_reschedule_in_progress` podem, em teoria, ser chamadas na mesma resposta do LLM (nada no `ToolNode` impede). O docstring de `confirm_appointment` já avisa para não usar force_encaixe se o paciente tem consulta futura — mas isso é uma instrução para o LLM, não uma trava no código. Se o LLM alucinar as duas chamadas juntas, pode gerar dois agendamentos. Vale um teste dirigido antes de decidir se merece uma trava de código.

### 🟡 Sem limite de tentativas quando a resposta do usuário não bate com nenhuma opção esperada [não re-verificado]
Em pontos de `app/graph/nodes.py` onde o bot pergunta "qual paciente? 1. Maria 2. João" e casa por palavra-chave, uma resposta fora do padrão ("o primeiro" / nome com erro de digitação) pode gerar um loop de reperguntas sem contador de tentativas nem fallback para atendente humano.

---

## 4. Webhook, Meta e Chatwoot

### 🔴 Nenhuma validação de assinatura `X-Hub-Signature-256` — **verificado**
Confirmado por grep: nenhuma ocorrência de `X-Hub-Signature`, `hub_signature` ou `hmac` em `app/main.py` ou `app/auth.py`. Os endpoints `/webhook` ([app/main.py:704-709](app/main.py:704)) e `/chatwoot-webhook` ([app/main.py:1199-1203](app/main.py:1199)) aceitam **qualquer POST**, sem checar que a origem é de fato a Meta.
- **Impacto real:** quem descobrir a URL do webhook (não é segredo — está em qualquer config pública do app na Meta, e é fácil de adivinhar padrões comuns) pode forjar mensagens de qualquer número de telefone, criar agendamentos falsos, manipular estado de conversa de pacientes reais.
- **Correção:** extrair o header `X-Hub-Signature-256`, calcular HMAC-SHA256 do corpo bruto da requisição com o app secret da Meta, comparar com `hmac.compare_digest`, rejeitar com 401 se não bater. Isso deve ser feito **antes** de qualquer parsing do payload.

### 🟠 Dedup por `message_id` existe, mas TTL da camada secundária (telefone+texto) é curto [parcialmente verificado]
**Verificado que existe** dedup primário por `msg_id` em [app/main.py:28](app/main.py:28) (`_is_duplicate`, cache com TTL, usado na linha 685). O agente também reportou uma segunda camada por `(phone, texto)` com TTL de ~10s como formato de proteção complementar contra retries que mudam o `msg_id` (não confirmei o TTL exato linha a linha, mas a existência do mecanismo primário está confirmada e é o item mais importante). Se o handler demorar mais que essa janela secundária (chamada LLM lenta), uma redelivery da Meta pode escapar da dedup textual — mas o dedup por `msg_id` já cobre o caso comum de retry idêntico.

### 🟡 Retorno rápido de 200 via `asyncio.create_task` — padrão correto, mas sem fila durável
[app/main.py:704-709](app/main.py:704): o webhook responde `{"status": "ok"}` imediatamente e processa em background via `asyncio.create_task`. **Isso é o padrão certo** para evitar que a Meta reenvie por timeout — não é um bug em si (corrigindo o enquadramento do achado original, que classificou isso como "HIGH problem"). O risco residual real é: se o processo cair *depois* do 200 mas *durante* o processamento em background, esse trabalho se perde, porque não há fila persistente (Redis/tabela de jobs) — só memória do processo. O código já tem uma tentativa de recuperação (`_recover_messages_lost_to_restart`, mencionada pelo agente), mas ela cobre só mensagens ainda dentro da janela de debounce do buffer, não as que já estavam em processamento.
- **Correção (se o risco for considerado alto o suficiente):** persistir a mensagem recebida antes de disparar o processamento, para poder reprocessar de forma idempotente após um crash.

### 🟡 Chamadas a Meta/Chatwoot sem retry em erro transitório [não re-verificado linha a linha]
`app/whatsapp.py` e `app/chatwoot.py` chamam `response.raise_for_status()` e propagam a exceção para cima sem retry/backoff. Se o Chatwoot cair por alguns segundos, a mensagem de confirmação para o paciente pode se perder sem segunda tentativa.

### 🟡 PII em logs de nível INFO [parcialmente verificado — padrão confirmado, ocorrências pontuais não]
Confirmei o padrão geral (uso de `logging` com `%s` para telefone/nome em várias partes do código, ex. `app/google_sheets.py`, `dashboard/attendant_routes.py:142`). Isso é esperado para rastreabilidade de incidentes (ítem 8 pedido pelo usuário), mas em um contexto de dado de saúde mental é sensível o suficiente para merecer uma política deliberada (ex.: nunca logar nome completo + motivo da consulta juntos; usar `patient_id` em vez de nome sempre que possível).

---

## 5. Segurança e LGPD

### 🔴 Números de telefone e nomes reais de pacientes hardcoded em scripts versionados — **verificado**
Confirmado: [scripts/_check_5581973260856.py:9](scripts/_check_5581973260856.py:9) tem `base = "5581973260856"` como literal — um número de telefone real de paciente. Esse padrão se repete em dezenas de arquivos `scripts/_check_*.py`, `_fix_*.py`, `_reschedule_*.py` (nomeados literalmente com o telefone do paciente). Esses arquivos estão **commitados no histórico do git**.
- **Impacto real:** qualquer pessoa com acesso ao repositório (incluindo histórico antigo, mesmo que o arquivo seja apagado depois) tem acesso a telefone e, em vários casos, nome completo de pacientes de uma clínica de psiquiatria — dado sensível sob a LGPD (dado de saúde).
- **Correção:** não é um problema de código a "consertar" com uma PR — é uma decisão de higiene de repositório: parar de commitar esses scripts com dado real (usar variável de ambiente ou argumento de linha de comando em vez de hardcode), e avaliar se vale reescrever o histórico do git ou pelo menos restringir o acesso ao repositório. Trato isso como item separado no plano, não uma "correção de código" comum.

### 🟠 Sem validação de env vars obrigatórias na subida do processo [não re-verificado a fundo]
`os.environ["SUPABASE_URL"]` e similares sem fallback — se faltar, o processo derruba com `KeyError` genérico, dificultando diagnóstico rápido em deploy.

### 🟡 Vazamento de detalhe de exceção para o cliente HTTP [não re-verificado]
Um endpoint (ex. envio manual de lembretes) reportado como retornando `detail=str(exc)` — pode vazar detalhe interno (nome de tabela, estrutura de erro do Supabase) para quem chama o endpoint.

### ✅ Sem segredos hardcoded encontrados
Grep por padrões de chave/token literal em `app/`, `dashboard/`, `.github/workflows/` não encontrou nada. `.env` existe em disco mas está no `.gitignore` (confirmar que nunca foi commitado antes, ver nota abaixo).

**Nota de atenção:** vale rodar `git log --all --full-history -- .env` para confirmar que o `.env` real nunca foi commitado no passado — não tive tempo de verificar isso nesta auditoria e é rápido de checar.

---

## 6. Confiabilidade em produção

### 🟠 Sem HEALTHCHECK no Dockerfile [não re-verificado a fundo]
Nenhum `HEALTHCHECK` nos Dockerfiles. Sem verificação de saúde, um processo travado (não crashado) não é reiniciado automaticamente pela plataforma (Easypanel).

### 🟡 Workflows do GitHub Actions sem `concurrency` guard [não re-verificado a fundo]
`appointment_reminders.yml`, `payment_reminders.yml`, `complete_appointments.yml` rodam em cron e têm `workflow_dispatch` habilitado — sem grupo de concorrência, um disparo manual durante a janela do cron poderia processar o mesmo lote duas vezes (lembretes duplicados, ou pior, ações de "completar consulta" duplicadas).

### ✅ Segredos dos workflows usam `${{ secrets.X }}` corretamente
Não encontrado nenhum valor hardcoded nos `.yml`.

---

## Resumo por severidade

| Severidade | Item |
|---|---|
| 🔴 Crítico | Sem validação de assinatura do webhook Meta ([app/main.py:704](app/main.py:704), [:1199](app/main.py:1199)) |
| 🔴 Crítico | RLS ausente em todas as tabelas com dado de paciente |
| 🔴 Crítico | Ordem Calendar-antes-do-Supabase em `confirm_appointment`, incluindo insert com `patient_id=NULL` sem falhar ([app/graph/tools.py:594-707](app/graph/tools.py:594)) |
| 🔴 Crítico | Telefone/nome real de paciente hardcoded em scripts versionados no git |
| 🔴 Crítico (grupo à parte) | 2 pontos de código ativo ainda usando `users` direto ([app/main.py:665](app/main.py:665), [dashboard/main.py:187](dashboard/main.py:187)) |
| 🟠 Alto | Race condition potencial entre checagem de conflito e criação de evento no Calendar |
| 🟠 Alto | Chave Supabase única sem diferenciar anon/service_role |
| 🟠 Alto | Exceções engolidas em `log_event`/`save_message` |
| 🟠 Alto | Sem retry/distinção de erro no refresh do token OAuth do Calendar |
| 🟠 Alto | `human_handoff` é um estado morto (armadilha para uso futuro) |
| 🟠 Alto | Sem HEALTHCHECK no Dockerfile |
| 🟡 Médio | Timezone naive em um ponto específico do Calendar |
| 🟡 Médio | Sem coluna dedicada de reconciliação do evento do Calendar |
| 🟡 Médio | Tool calls conflitantes no mesmo turno (confirm + reschedule) |
| 🟡 Médio | Sem limite de tentativas em pergunta não reconhecida pelo bot |
| 🟡 Médio | Sem fila durável para o processamento em background do webhook |
| 🟡 Médio | Sem retry em chamadas Meta/Chatwoot |
| 🟡 Médio | PII em logs INFO (padrão geral, requer política) |
| 🟡 Médio | Sem validação de env vars obrigatórias na subida |
| 🟡 Médio | Vazamento de detalhe de exceção para cliente HTTP |
| 🟡 Médio | Sem `concurrency` guard nos workflows do GitHub Actions |
| ⚪ Baixo | Doctor IDs hardcoded em `database.py` |
| ~~Descartado~~ | ~~IndexError por `.data[0]` desprotegido~~ — verificado, já tem guard em todos os 4 casos apontados |
| ~~Rebaixado~~ | ~~Race condition no buffer de mensagens~~ — verificado, protegido por `phone_lock` + falta de `await` intermediário |

---

## Plano de correção proposto (Tarefa 2 — aguardando aprovação)

A ordem segue: **crítico → alto → médio**, com o grupo da tabela `users` isolado conforme pedido, para validação item a item antes de aplicar.

### Grupo A — Crítico, sem tocar em `users` (pode começar primeiro)
1. Assinatura do webhook Meta (`X-Hub-Signature-256`) — maior exposição de segurança, correção isolada e de baixo risco de quebrar o fluxo existente.
2. RLS nas tabelas de paciente — requer decidir política de acesso junto com você antes de escrever as policies (não é só "ligar RLS", tem que garantir que o backend continua funcionando com a chave certa).
3. Ordem Calendar/Supabase em `confirm_appointment` + parar de inserir `patient_id=NULL` silenciosamente — este é o mais delicado de mexer porque toca o core do fluxo de agendamento; proponho TDD (escrever teste do cenário de falha antes de alterar).
4. Higiene dos scripts com dado real de paciente no git — decisão sua sobre reescrever histórico ou só parar de commitar novos.

### Grupo B — Tabela `users` (grupo separado, validar cada substituição antes de aplicar)
5. [app/main.py:665](app/main.py:665) — trocar DELETE em `users` pelo equivalente em `contacts`/`patients` no endpoint de reset.
6. [dashboard/main.py:187](dashboard/main.py:187) — trocar SELECT em `users` por `contacts`.
7. (Opcional, depois que A e B estiverem estáveis) revisar se vale rodar o backfill final e remover os shims de `app/database.py`.

### Grupo C — Alto
8. Separar `SUPABASE_ANON_KEY`/`SUPABASE_SERVICE_ROLE_KEY`.
9. Parar de engolir exceção em `log_event`/`save_message` (logar no mínimo).
10. Distinguir erro de auth vs. transitório no refresh do token do Calendar.
11. Resolver ou remover o estado morto `human_handoff`.
12. Adicionar HEALTHCHECK ao Dockerfile.
13. Investigar a fundo (com teste dirigido) a race condition do guard de conflito de horário no Calendar antes de decidir a correção.

### Grupo D — Médio (posso agrupar vários numa mesma leva de PRs pequenos)
14–21. Itens médios da tabela acima, em qualquer ordem — nenhum deles isoladamente é urgente.

---

Aguardando sua aprovação para começar pelo Grupo A (ou pela ordem que preferir). Nenhuma alteração foi feita ainda.
