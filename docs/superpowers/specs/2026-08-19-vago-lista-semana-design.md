# Expressão vaga de dia → relação de horários (em vez de perguntar)

**Data:** 2026-08-19
**Branch:** `vago-lista-semana`

## Problema

Hoje, quando o paciente responde de forma vaga sobre quando quer a consulta
(ex.: "próxima semana", "semana que vem"), a Eva **pergunta qual dia da semana**
ele prefere antes de buscar horários. Isso gera ida-e-volta desnecessário: o
paciente já sinalizou uma janela, e o esperado seria a Eva **já mandar a relação
de horários disponíveis** daquela janela.

Esse comportamento é proposital e está codificado em **dois lugares**:

1. **Prompt** — [app/graph/prompts.py:1221](../../../app/graph/prompts.py) e a
   duplicata em [app/graph/prompts.py:1429](../../../app/graph/prompts.py):
   instruem a Eva, ao ouvir "próxima semana", a consultar a grade do médico e
   **perguntar qual dia** antes de chamar `get_available_slots`.
2. **Tool** — [app/graph/tools.py](../../../app/graph/tools.py), no branch
   `_vague_patterns = ("semana", "em breve")` de `_get_available_slots_impl`,
   que devolve a instrução `CLARIFICAÇÃO NECESSÁRIA` mandando perguntar o dia.

## Objetivo

Quando o paciente for **vago** sobre o dia, a Eva **oferece a relação de
horários** em vez de perguntar. Quando o paciente pede um **dia específico**
("quero segunda"), **nada muda** — o fluxo atual é preservado.

Escopo escolhido no brainstorming:
- **Só o caso vago** dispara a lista (não em todo início de agendamento).
- Comportamento **misto**: se o paciente cita uma semana específica, a lista
  respeita essa semana; se for genérico ("quando puder", "em breve"), usa os
  "próximos dias com vaga".

## Comportamento (roteamento do caso vago)

No `_get_available_slots_impl`, o branch que hoje devolve `CLARIFICAÇÃO
NECESSÁRIA` passa a rotear:

| Paciente diz | Rota | Janela mostrada |
|---|---|---|
| "próxima semana", "semana que vem", "semana seguinte" | `_search_week(1)` | seg–sex da semana **seguinte** |
| "essa semana", "esta semana" | `_search_week(0)` | dias úteis **restantes** desta semana |
| "em breve", "quando puder", vago genérico | `_search_any_day` (atual) | próximos dias úteis com vaga |
| "qualquer dia", "tanto faz" | `_search_any_day` (já hoje) | inalterado |

**Convenção mantida:** "próxima semana" = segunda a sexta da semana seguinte,
nunca um dia que ainda caia na semana atual (linhas 1222/1430 do prompt).

**Preferência de turno (`preferred_shift`) é preservada** em todas as rotas — se
o paciente disse "próxima semana de manhã", só horários da manhã entram na lista.

**Fallback de segurança:** se a semana alvo (`_search_week`) não tiver **nenhuma**
vaga, cai para `_search_any_day`, para nunca terminar com "não encontrei nada"
quando existe horário em outra semana. Mesmo princípio já usado hoje pelo
`_search_any_day` (nunca informa "não encontrei" enquanto houver algo adiante).

## Componentes

### 1. Tool — `app/graph/tools.py`

**Novo helper `_search_week(week_offset, calendar_id, doctor, preferred_shift,
slot_duration_minutes)`:**

- Usa `_week_range(week_offset)` (já existe) para obter o intervalo da semana.
- Para `week_offset == 0`, itera apenas os dias úteis **restantes** (a partir de
  hoje; a antecedência mínima de ~4h já é aplicada por `get_available_slots` a
  nível de slot, então dias/horários no passado simplesmente não aparecem).
- Para cada dia útil, reusa `_slots_for_any_day(...)` (já existe) e coleta os
  dias que têm vaga — **sem** o teto de 3 dias do `_search_any_day` (a semana já
  é um intervalo limitado).
- Formata com `_format_any_day_section(...)` (já existe), uma seção por dia.
- Reaproveita o prefetch de ocupação do Supabase (`_prefetch_supabase_busy`)
  como o `_search_any_day` faz, para não repetir chamadas por dia.
- Retorna a string pronta (mesma forma que `_search_any_day`), ou — se a semana
  estiver vazia — delega a `_search_any_day` (fallback).

**Roteamento no `_get_available_slots_impl`:** substituir o branch
`_vague_patterns` que devolve `CLARIFICAÇÃO NECESSÁRIA` por:

- `weekday_key is None` **e** a expressão contém "próxima semana" / "semana que
  vem" / "semana seguinte" → `_search_week(1)`.
- `weekday_key is None` **e** contém "essa semana" / "esta semana" →
  `_search_week(0)`.
- `weekday_key is None` **e** contém "em breve" / demais expressões vagas →
  `_search_any_day` (comportamento atual, sem CLARIFICAÇÃO).

O ramo de `weekday_key is not None` (paciente citou um dia da semana, inclusive
"quarta da próxima semana", que `_parse_day` já resolve) permanece **intocado**.

### 2. Prompt — `app/graph/prompts.py`

Reescrever as linhas 1221 e 1429 (o texto está duplicado em dois blocos de
fluxo) de:

> "…consulte DIAS DE ATENDIMENTO / HORÁRIOS DE ATENDIMENTO acima e pergunte qual
> dia prefere … ANTES de chamar get_available_slots."

para instrução equivalente a:

> Se o paciente disser "próxima semana", "semana que vem", "essa semana" ou
> expressão de semana sem citar um dia, **chame `get_available_slots` passando a
> expressão em `preferred_day`** (ex.: `preferred_day="próxima semana"`) e
> apresente a relação de horários retornada — **não** pergunte qual dia prefere.

Manter:
- A convenção "próxima semana = seg–sex seguinte" (1222/1430).
- A regra da "próxima quarta / ocorrência seguinte" (1223/1431), que trata dia
  **específico** e continua válida.
- O aviso ⚠️ de disponibilidade simultânea ao montar a mensagem ao paciente
  (memória `feedback_availability_disclaimer`).

A linha 1224/1432 ("Se retornar CLARIFICAÇÃO NECESSÁRIA, pergunte o dia")
permanece como backstop genérico — a tool deixa de emitir CLARIFICAÇÃO para
expressões de semana, mas a instrução segue válida caso a tool a emita em outro
contexto.

## Testes — `tests/`

`tests/test_tools.py` (segue o padrão dos casos "qualquer dia" já existentes, com
`get_available_slots` mockado por dia):

- "próxima semana" → chama `_search_week(1)`, lista só dias da semana seguinte.
- "essa semana" → `_search_week(0)`, só dias úteis restantes desta semana.
- "em breve" (vago genérico) → cai em `_search_any_day` (não retorna
  CLARIFICAÇÃO).
- Preferência de turno preservada (ex.: "próxima semana de manhã" só traz manhã).
- Fallback: semana alvo sem vaga → delega a `_search_any_day` e ainda devolve
  horários de outra semana.

Ajustar qualquer teste existente que espere `CLARIFICAÇÃO NECESSÁRIA` para
"próxima semana" (ver `tests/test_calendar.py` em torno das asserções de
"próxima semana", e `tests/test_tools.py`).

## Fora de escopo

- Não muda o fluxo quando o paciente pede um **dia específico**.
- Não muda o `_search_any_day` nem os limites `_ANY_DAY_*`.
- Não introduz proatividade "sempre mostrar a lista no início do agendamento".
