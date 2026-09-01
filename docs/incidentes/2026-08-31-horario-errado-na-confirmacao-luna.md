# Horário errado no resumo de confirmação (pós-troca para gpt-5.6-luna)

## O que aconteceu

Em **31/08/2026** a Eva enviou o resumo "Só confirmar antes de registrar" com um
**horário diferente do que o paciente escolheu**, em três conversas distintas no
mesmo dia:

- **Pedro Affonso Corrêa Frazão** (`5521981196718`) — pediu quarta 02/09 às **14h**.
  A Eva confirmou 14h, depois trocou para **11:00** num reenvio e ainda abriu um
  fluxo de **remarcação fantasma** (ofereceu 09h/10h/11h) para uma consulta que já
  estava registrada. O paciente corrigiu ("A consulta não é às 14h?") e a Eva voltou
  atrás.
- **João Pedro Ferreira Victor** (`5581991542212`) — pediu "16/09, **11h** presencial".
  A Eva confirmou "às **08:00**". O paciente respondeu "Sim pode confirmar" sem
  perceber o erro.
- **Paula Muniz Evangelista**, pela mãe **Glória** (`5581991270824`) — pediu "dia 3
  às **11h**". A Eva confirmou "às **08:00**".

Nos dois últimos, o horário confirmado (08:00) **nem estava na lista de horários
oferecida** — é o primeiro slot da grade padrão. Indício de que o modelo, sem
raciocínio, "chuta" o horário mais cedo da grade em vez de usar o que o paciente
falou.

## Impacto real

**Nenhuma vaga ficou marcada errada.** Nos três casos o `appointments.start_time`
no banco está correto:

- Pedro: `0vuiun0s4k391ei9v5hd57dmjk` → 02/09 14:00 (17:00 UTC)
- João Pedro: `gn9cqai0jrc8cu12d1p42s698o` → 16/09 11:00 (14:00 UTC)
- Paula: `fkegs8t6f3kctiomu2scvjs9cs` → 03/09 11:00 (14:00 UTC)

O erro ficou **só no texto** do resumo. O estrago é o paciente ver um horário errado
e se assustar (o Pedro teve que corrigir e passou por uma remarcação que não existia).

## Correlação com a troca de modelo

Produção roda `gpt-5.6-luna` com `reasoning_effort="none"` desde **27/08/2026**
(PR #188), em `app/graph/nodes.py:182` e `:191`.

Comparação com sinal limpo (paciente diz um horário explícito e a Eva confirma
outro), via `scripts/_audit_confirm_mismatch_range.py`:

| Janela            | Conversas | Casos genuínos | Taxa aprox. |
|-------------------|-----------|----------------|-------------|
| 01/07 → 27/08 (pré-luna) | 266 | ~2 | ~0,75% em 8 semanas |
| 27/08 → 31/08 (pós-luna) | 49  | 3  | ~4–6% em 4 dias |

Direção consistente (≈5x mais alto pós-luna) e assinatura típica (08:00 da grade).
**Indício forte, não prova** — amostra de 4 dias e 49 conversas.

## Causa raiz (mecanismo no código)

O resumo de confirmação é **prosa livre gerada pelo modelo** (`app/graph/prompts.py:546`),
não montado por código a partir do slot escolhido. O prompt já tinha um aviso contra
o flip de -3h (`prompts.py:553`), sinal de que o problema não é novo — só ficou mais
frequente com o modelo mais barato.

Pior: `_extract_pending_appointment` (`app/graph/nodes.py:1673`) faz **parse da prosa**
para montar `pending_appointment.slot_datetime` (`nodes.py:2906`), e o atalho
programático de confirmação (`nodes.py:1954`) chama `confirm_appointment` a partir
desse campo. Ou seja, existe um caminho em que **o horário errado da prosa poderia
virar agendamento errado no banco** se o paciente confirmasse na sequência. Nos três
casos conhecidos o banco ficou certo — falta explicar em runtime por quê (provável
tool-call correto do modelo apesar da prosa errada), mas o risco existe.

## Confirmação da causa raiz (checkpoint)

O checkpoint do João Pedro mostra a chamada de `confirm_appointment` com `args={}`
vazio — a assinatura do atalho programático (`nodes.py` injeta a tool com args
vazios) — e o resultado da tool "às **11:00**". Ou seja, o `pending_appointment`
segurava o horário certo (11:00) enquanto o **texto** exibido dizia 08:00. Confirma
que **o banco nunca foi corrompido** nos três casos e que o bug é o horário exibido
no resumo, escrito como texto livre pelo modelo.

## Correção (implementada)

Guarda de defesa em profundidade em `patient_agent_node`, aplicada ao texto do
resumo **antes** de enviar ao paciente e antes de virar `pending_appointment`:
`_correct_confirmation_summary_time` (`app/graph/nodes.py`).

A guarda cruza o horário do resumo com (a) os horários realmente ofertados pelo
`get_available_slots` no histórico e (b) o horário que o paciente pediu nas últimas
mensagens. Só corrige quando há um **alvo único e confiável** — a interseção entre o
que o paciente pediu e o que foi ofertado. Sem oferta rastreável (encaixe, instrução
da atendente) ou sem alvo único, não altera nada, para não quebrar fluxos válidos.

Assim, o que o paciente lê passa a bater sempre com o horário que será agendado.

Testes: `test_confirm_guard_*` e
`test_patient_agent_confirm_guard_fixes_sent_summary_and_pending` em
`tests/test_process_message.py` (unitários da lógica + integração pelo node).

**Não** subimos `reasoning_effort` — a guarda resolve o sintoma sem reintroduzir
custo (ver memória `acompanhar-economia-api-apos-luna`).

## Ação manual no banco

**Nenhuma necessária** — os três agendamentos já estão no horário correto.

## Scripts de auditoria

- `scripts/_audit_confirm_mismatch_range.py` — paciente diz horário X, Eva confirma
  Y (aceita `SINCE`/`UNTIL` por env var).
- `scripts/_audit_time_flip_since_luna.py` — flip de horário entre confirmações na
  mesma conversa.
- `scripts/_audit_phantom_reschedule_since_luna.py` — remarcação disparada logo após
  agendamento novo.
