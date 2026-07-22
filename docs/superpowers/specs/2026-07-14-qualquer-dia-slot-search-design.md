# Design: tratamento de "qualquer dia" na busca de horários

## Problema

Quando o paciente responde "qualquer dia" à pergunta da Eva sobre qual dia da semana
prefere, o comportamento atual é incorreto: a Eva volta a perguntar "qual dia da semana
você prefere?", ignorando que o paciente já disse que não tem preferência.

## Causa raiz

Em `app/graph/tools.py:233`, a tool `get_available_slots` tem:

```python
_vague_patterns = ("semana", "mês", "mes", "em breve", "qualquer", "tanto faz")
if weekday_key is None and any(p in preferred_day_norm for p in _vague_patterns):
    return (
        "CLARIFICAÇÃO NECESSÁRIA: O paciente disse uma expressão vaga (ex: 'próxima semana'). "
        "Pergunte qual dia da semana prefere (segunda a sexta) antes de chamar get_available_slots novamente."
    )
```

`"qualquer"` e `"tanto faz"` estão misturados com expressões genuinamente ambíguas
("próxima semana", "mês", "em breve") que exigem esclarecimento. Mas "qualquer dia" não
é ambíguo — é uma resposta completa que significa "sem preferência de dia, me mostre o
que houver". O código trata as duas situações da mesma forma, gerando o loop de
reperguntar o dia.

Não existe hoje nenhum caminho de busca "vários dias, qualquer dia da semana" — toda a
lógica de busca multi-semana existente (`week_offset in range(4)`) é sempre ancorada em
um dia da semana específico (`weekday_key`).

## Comportamento desejado

### 1. `app/graph/tools.py` — novo branch de busca "qualquer dia"

Separar `"qualquer"` e `"tanto faz"` de `_vague_patterns`. Esse tuple passa a conter
apenas `("semana", "mês", "mes", "em breve")` — continuam exigindo esclarecimento como
hoje.

Novo branch, disparado quando `weekday_key is None` e `preferred_day_norm` casa com um
padrão de "sem preferência de dia" (`"qualquer dia"`, `"qualquer"`, `"tanto faz"`, "sem
preferência"):

**Fase 1 — semana atual:**
- Percorre dia a dia a partir de hoje até domingo desta semana.
- Pula dias em que o médico não atende, consultando `DOCTOR_SCHEDULES` antes de chamar
  o Google Calendar (evita chamadas desnecessárias).
- Para cada dia candidato, busca horários:
  - no turno informado, se `preferred_shift` for específico; ou
  - nos 3 turnos (manhã/tarde/noite), se `preferred_shift="qualquer"` — reaproveitando o
    padrão de detalhamento por turno já usado nas linhas 183–194 do arquivo.
- Junta até **3 dias distintos com pelo menos 1 horário disponível**.

**Critério de disparo da Fase 2:**
- Se o número de dias distintos com horário encontrados na Fase 1 for `< 2`, passa para
  a Fase 2.

**Fase 2 — semana seguinte:**
- Mesma varredura, de segunda a domingo da semana seguinte.
- Junta **todos** os dias úteis com horário disponível (sem limite de 3 desta vez).
- Resultado é somado ao da Fase 1.

**Fase 3 — fallback de segurança (caso raro):**
- Se mesmo somando as duas semanas não houver nenhum horário, a busca continua
  expandindo semana a semana (4ª, 5ª, 6ª... até um limite de segurança de ~8 semanas,
  só para evitar loop infinito/timeout).
- A Eva **nunca** deve dizer ao paciente "não encontrei horários" nesse fluxo — a busca
  deve continuar até encontrar algo disponível.

**Antecedência mínima:** horários de hoje muito próximos já são excluídos pelo filtro de
4h existente em `google_calendar.get_available_slots:500` — não precisa de tratamento
especial para "hoje" neste branch.

**Formato de saída:** reaproveita as convenções já existentes na função — cabeçalho por
dia (`{dia da semana}, dia DD/MM`) com horários numerados `HH:MM [modalidade]`, ou seções
por turno quando o turno ainda é desconhecido (mesmo padrão do branch
`preferred_shift == "qualquer"` já existente). Se a Fase 2 (ou além) foi necessária, o
texto retornado pela tool inclui uma nota interna curta indicando isso (ex: "Poucos
horários esta semana — incluindo também a semana seguinte:"), para a LLM repassar esse
contexto ao paciente.

### 2. `app/graph/prompts.py` — instrução nos dois blocos de prompt

Há dois blocos quase idênticos de instruções de agendamento: um no fluxo de paciente
recorrente (por volta da linha 818–831) e seu gêmeo em `NEW_PATIENT_SYSTEM` (por volta da
linha 971–982). Ambos precisam do mesmo ajuste.

Adicionar um bullet próximo à instrução existente sobre "próxima semana" (linha 826/977):
quando o paciente disser "qualquer dia", "tanto faz", "não tenho preferência de dia" ou
equivalente, a Eva deve chamar `get_available_slots` passando `preferred_day="qualquer
dia"` diretamente — **sem** perguntar por um dia da semana específico antes. A tool cuida
da busca multi-dia/multi-semana automaticamente.

### 3. Testes (`tests/test_tools.py`)

Adicionar casos cobrindo:
- Muitos horários na semana atual → resposta usa só a semana atual, sem disparar busca
  na semana seguinte.
- 0–1 dia distinto com horário na semana atual → horários da semana seguinte aparecem
  somados na resposta.
- Duas semanas seguidas totalmente vazias (mockado) → a busca continua até encontrar
  horários numa 3ª semana, e a resposta nunca contém uma mensagem de "não encontrei".

## Fora de escopo

- Não altera o comportamento para dias da semana específicos (`weekday_key is not None`)
  nem para expressões genuinamente ambíguas ("próxima semana", "mês") — esses fluxos
  continuam como estão.
- Não altera a UI/formatação usada pelo script `doctor-availability-format` (uso da
  atendente, fluxo separado).
