# Âncora de mês no agendamento de retorno

**Data:** 2026-08-11
**Autor:** Ayexa (via Claude)

## Problema

O sistema de lembrete de retorno (`scripts/send_return_reminders.py`) envia três
avisos por ciclo, relativos ao `next_return_date` do paciente:

- `retorno_mes_anterior` — a previsão é o **mês que vem**;
- `retorno_no_mes` — a previsão é o **mês corrente**;
- `retorno_atrasado` — a previsão **já passou**.

O texto do lembrete é injetado no checkpoint do LangGraph como `AIMessage`, então
a Eva "vê" o aviso na história — mas não recebe nenhuma informação estruturada
sobre a previsão. O prompt da Eva (`EXISTING_PATIENT_SYSTEM`) é remontado a cada
mensagem com um CALENDÁRIO DE REFERÊNCIA ancorado em **hoje**, e não conhece o
`next_return_date`.

Resultado: quando o paciente responde ao lembrete com uma data "solta" (sem citar
o mês), a Eva ancora no mês corrente.

**Caso real (11/08/2026):** paciente recebeu o `retorno_mes_anterior` (hoje =
agosto, previsão = setembro), pediu "quero ver depois do dia 25" querendo dizer
25/09, e a Eva buscou slots em 25/08.

## Objetivo

A Eva deve interpretar datas soltas dadas em resposta a um lembrete de retorno
ancorando no **mês da previsão de retorno**, não no mês corrente — sempre
respeitando um ajuste explícito do paciente ("não, ainda quero em agosto", "só
consigo em outubro").

## Decisão de design

Aprovada a **Abordagem A**: injetar o mês da previsão, já computado em Python,
como um rótulo pronto no system prompt da Eva.

Isso segue a filosofia já presente no código: a Eva **nunca** é encarregada de
fazer conta de calendário (o prompt proíbe isso repetidamente — "LLMs erram
calendário" — e entrega rótulos pré-computados). A alternativa descartada
(Abordagem B, regra só de prompt pedindo à Eva para inferir o mês a partir do
texto do lembrete) exigiria que ela calculasse mês+1 a partir de "mês que vem",
exatamente a conta proibida.

## Regra de âncora

Dado o `next_return_date` e a data de hoje, o **mês-âncora** é:

> `mes_ancora = o mais tarde entre (mês corrente, mês de next_return_date)`

Isso cobre os três casos com uma só regra, sem nunca ancorar no passado:

| Lembrete enviado   | next_return_date | hoje    | mes_ancora |
|--------------------|------------------|---------|------------|
| `mês_anterior`     | set/2026         | ago/2026| **set/2026** (mês que vem) |
| `no_mes`           | set/2026         | set/2026| **set/2026** (corrente)    |
| `atrasado`         | jul/2026         | set/2026| **set/2026** (o quanto antes) |

## Quando injetar

O rótulo só entra no prompt quando há um **ciclo de lembrete ativo** para o
paciente com o médico atual:

1. Existe linha em `return_reminders` para `(patient_id = state["user_db_id"],
   doctor_id = DOCTOR_IDS[preferred_doctor])`.
2. Pelo menos um lembrete já foi enviado no ciclo (`month_before_sent_at`,
   `month_of_sent_at` ou `overdue_sent_at` não nulo) — sem envio, não há contexto
   de retorno a ancorar.
3. A classificação **não está obsoleta**: reaproveita a mesma checagem do
   `send_return_reminders._is_stale_classification` — se a consulta
   agendada/concluída mais recente do paciente com o médico for diferente de
   `last_classified_appointment_id`, o retorno já foi (re)agendado ou o médico
   ainda não reclassificou; nesse caso **não injeta** (evita ancorar num ciclo
   já resolvido).

Fora dessas condições, nenhuma linha é adicionada e o agendamento normal segue
inalterado.

## Texto injetado

Novo placeholder `{return_prediction_rule}` nos templates `EXISTING_PATIENT_SYSTEM`
e `NEW_PATIENT_SYSTEM` (string vazia quando não há ciclo ativo). Conteúdo quando
ativo, com os meses já resolvidos em Python (ex.: previsão set/2026, âncora
set/2026):

> CONTEXTO DE RETORNO: o retorno deste paciente está previsto para **setembro de
> 2026**. Se o paciente responder com uma data ou período sem citar o mês (ex:
> "depois do dia 25", "lá pelo dia 10", "na segunda semana"), assuma que ele se
> refere a **setembro de 2026**, e NÃO ao mês corrente — a menos que ele peça
> explicitamente outro mês (ex: "ainda esse mês", "só em outubro"), caso em que
> você respeita o pedido dele. Ao chamar get_available_slots, use o mês-âncora
> correto.

Para o caso `atrasado` (previsão já passou), o texto acrescenta que a previsão já
passou e a âncora é o quanto antes, mas ainda entrega o mês-âncora concreto.

## Componentes

1. **`get_active_return_prediction(user_db_id, doctor_id)`** — novo helper
   (provável casa: `app/patients.py`, junto de `get_contacts_for_patient`).
   Faz o lookup em `return_reminders`, aplica as condições de "ciclo ativo"
   (incluindo a checagem de classificação obsoleta), e retorna algo como
   `{"prediction_date": date, "anchor_month": (ano, mes), "overdue": bool}` ou
   `None`. Retorna `None` em qualquer erro/ausência de dado (fail-open: sem
   rótulo, comportamento atual).

2. **`_build_return_prediction_rule(prediction) -> str`** — função pura que
   formata o texto acima a partir do dict do helper (usa nomes de mês em
   português de `app/dates.py`). Retorna `""` para `None`.

3. **`patient_agent_node`** (`app/graph/nodes.py`) — chama o helper (só quando
   `state.get("user_db_id")` e `preferred_doctor` existem) e passa
   `return_prediction_rule=...` para `template.format(...)`.

4. **Templates** (`app/graph/prompts.py`) — adicionar `{return_prediction_rule}`
   perto da seção de agendamento em ambos os templates.

## Tratamento de erro

- Lookup falho ou linha ausente → `None` → rótulo vazio → agendamento normal.
  O recurso é aditivo; nunca deve quebrar o fluxo de agendamento.

## Testes

Seguindo a estrutura de `tests/` (uma camada por arquivo):

- **`test_process_message.py`** (camada de nodes):
  - `_build_return_prediction_rule` é pura → testar as três variações
    (mês anterior, no mês, atrasado) e o `None → ""`.
  - Teste com mock de Supabase para `get_active_return_prediction`: linha ativa
    → dict correto; sem envio → `None`; classificação obsoleta → `None`.
  - Teste de integração mockado do `patient_agent_node`: com ciclo ativo, o
    system prompt contém "CONTEXTO DE RETORNO" e o mês-âncora certo; sem ciclo,
    não contém.

## Fora de escopo

- Não altera o envio dos lembretes nem a classificação no dashboard.
- Não muda o parsing de datas explícitas (paciente que cita o mês continua
  sendo respeitado — isso é o comportamento atual e desejado).
