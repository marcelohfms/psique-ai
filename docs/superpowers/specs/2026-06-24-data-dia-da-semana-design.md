# Design — Blindagem de data, dia da semana e hoje/amanhã da Eva

**Data:** 2026-06-24
**Status:** Aprovado (aguardando plano de implementação)

## Problema

Em mensagens conversacionais, a Eva (LLM `gpt-4.1`) erra com frequência o **dia
da semana** de uma data e a relação **hoje/amanhã** de uma consulta. A causa raiz
é arquitetural: o prompt instrui o LLM a *calcular* essas informações —
comparar a data da consulta com a data atual e inferir o dia da semana. LLMs
cometem erros de calendário, fato que o próprio prompt já admite
(`app/graph/prompts.py:696`), mas ainda assim o cálculo é exigido em vários
pontos.

Os lembretes **proativos** (cron `scripts/send_appointment_reminders.py`) já são
determinísticos — "hoje"/"amanhã" são fixados em Python conforme qual cron
dispara — e **não** fazem parte do escopo deste trabalho. O problema está nas
respostas conversacionais geradas em `app/graph/nodes.py` / `app/graph/prompts.py`.

## Princípio da solução

Não adicionar mais instrução. Fazer o **oposto**: calcular os fatos de calendário
em Python e injetá-los prontos no prompt, de modo que a Eva nunca precise fazer
conta de calendário. Para datas distantes (fora da janela de referência), oferecer
uma ferramenta determinística. O resultado **reduz** o tamanho das instruções.

Escopo escolhido: **qualquer data** que a Eva mencione na conversa (não apenas
consultas) — abordagem híbrida (bloco de referência + ferramenta).

## Componentes

### 1. `app/dates.py` — módulo determinístico isolado

Centraliza toda a aritmética de calendário (hoje espalhada em `nodes.py`).
Funções puras, fáceis de testar isoladamente:

- `weekday_pt(d: date) -> str` — `"terça-feira"`. Substitui a lista inline de
  `nodes.py:1052`.
- `relative_label(target: date, today: date) -> str | None` — `"hoje"`,
  `"amanhã"`, `"depois de amanhã"`, ou `None` para demais datas.
- `format_date_pt(target: date, today: date) -> str` — combina relativo + dia da
  semana: `"25/06 (amanhã, quarta-feira)"`; sem relativo: `"15/09 (terça-feira)"`.
- `build_date_reference(now: datetime) -> str` — monta o bloco de referência dos
  próximos 14 dias (ver abaixo).

Timezone de referência: `America/Recife` (consistente com o resto do código).

### 2. Bloco de referência injetado no prompt

Em `app/graph/nodes.py`, o valor de `today` passado ao template
(`nodes.py:1053`, usado em ambos `NEW_PATIENT_SYSTEM` e `EXISTING_PATIENT_SYSTEM`
via placeholder `{today}` — `prompts.py:630` e `:802`) passa a incluir o bloco
produzido por `build_date_reference(now)`:

```
Data e hora atual (America/Recife): 24/06/2026 14:30 (terça-feira).

CALENDÁRIO DE REFERÊNCIA — use SEMPRE estes rótulos prontos. NUNCA calcule
dia da semana nem hoje/amanhã por conta própria:
  hoje    = 24/06 (terça-feira)
  amanhã  = 25/06 (quarta-feira)
            26/06 (quinta-feira)
            27/06 (sexta-feira)
            ... (até 14 dias à frente)
```

Janela: **14 dias** a partir de hoje (inclusive).

### 3. Rótulos nas consultas agendadas

O bloco que injeta consultas agendadas (`nodes.py:1156-1174`, especificamente a
linha `label = f"- {dt.strftime('%d/%m/%Y às %H:%M')} ..."` em `nodes.py:1166`)
passa a enriquecer cada linha com relativo + dia da semana via `app/dates.py`:

```
- 25/06/2026 às 15:00 (amanhã, quarta-feira) (ID: abc123) ⚠️ TAXA DE RESERVA PENDENTE
```

A tag de taxa pendente e a separação future/recent permanecem inalteradas.

### 4. Ferramenta `consultar_data`

Em `app/graph/tools.py`, nova tool determinística (delega para `app/dates.py`),
registrada em `TOOLS` (`nodes.py:26-33`):

```python
@tool
async def consultar_data(data: str) -> str:
    """Retorna o dia da semana e a relação com hoje (hoje/amanhã/em N dias)
    de uma data. Use SEMPRE que precisar mencionar o dia da semana de uma
    data fora do calendário de referência (mais de 14 dias à frente)."""
```

- Entrada aceita `"dd/mm"` (ano inferido: próximo ano em que a data ocorre a
  partir de hoje, consistente com a lógica de `nodes.py:747-752`) ou
  `"dd/mm/aaaa"`.
- Saída: `"15/09/2026 é uma terça-feira (em 83 dias)"`. Para hoje/amanhã usa o
  rótulo relativo (`"... (hoje)"` / `"... (amanhã)"`).
- Entrada inválida: retorna mensagem de erro amigável pedindo o formato correto
  (não levanta exceção).

### 5. Enxugar instruções de cálculo

Remover/encurtar os blocos que pedem cálculo ao LLM, substituindo por uma regra
curta única que aponta para o calendário de referência, os rótulos das consultas
e a ferramenta `consultar_data`:

- `prompts.py:696` e `:837` — o longo "NUNCA calcule ou infira o dia da
  semana...".
- `prompts.py:739-743` e `:899-903` — o "Para saber se diz 'hoje' ou 'amanhã':
  compare a DATA da consulta com a DATA ATUAL...".

Regra substituta (uma vez por template):
> "Para qualquer dia da semana ou hoje/amanhã, use os rótulos já prontos no
> CALENDÁRIO DE REFERÊNCIA e nas consultas agendadas. Para datas além de 14 dias,
> chame `consultar_data`. NUNCA calcule por conta própria."

As regras de *uso* dos rótulos nas respostas de confirmação de presença
(`prompts.py:744-745`, `:904-905`) permanecem — apenas deixam de pedir o cálculo,
passando a referenciar os rótulos prontos.

## Fluxo de dados

```
process_message node (nodes.py)
  → now = datetime.now(America/Recife)
  → build_date_reference(now)  ──┐
  → consultas agendadas          ├─→ system_prompt (com fatos prontos)
     enriquecidas via dates.py ──┘
  → LLM responde lendo rótulos (não calcula)
  → se data > 14 dias: LLM chama consultar_data → app/dates.py (determinístico)
```

## Tratamento de erros

- `consultar_data` com entrada inválida: retorna string de orientação, sem
  exceção (mesmo padrão das demais tools).
- `build_date_reference` é puro e total — não falha para `now` válido.
- Virada de mês/ano e DST tratados pela aritmética de `date`/`timedelta` em
  timezone `America/Recife`.

## Testes (conforme CLAUDE.md)

- **Novo `tests/test_dates.py`** (módulo novo justifica arquivo novo): testes
  unitários puros — dia da semana correto em múltiplos casos; hoje/amanhã/depois
  de amanhã; virada de mês e de ano; janela de 14 dias do `build_date_reference`;
  timezone America/Recife.
- **`tests/test_tools.py`**: testes da `consultar_data` — formatos `dd/mm` e
  `dd/mm/aaaa`, inferência de ano, data inválida, rótulo relativo correto.
- **`tests/test_process_message.py`**: o bloco de referência e os rótulos
  (relativo + dia da semana) das consultas agendadas aparecem no system prompt
  construído.

## Fora de escopo

- Lembretes proativos do cron (já determinísticos).
- Alterar o modelo do LLM.
- Refatorações não relacionadas em `nodes.py`/`prompts.py`.
