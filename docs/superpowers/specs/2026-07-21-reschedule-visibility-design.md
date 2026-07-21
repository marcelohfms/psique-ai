# Visibilidade de remarcação pendente no contexto da Eva

## Contexto

Em 21/07/2026, um paciente (Heitor, contato Ludmilla, 5581996937559) teve o slot de
consulta liberado por `release_pending_reschedules.py` em 30/06 (`status` →
`pending_reschedule`, taxa de reserva preservada). A Ludmilla só voltou a falar com a
Eva quase três semanas depois. Sem nenhum sinal de que existia uma remarcação
pendente, a Eva conduziu a conversa como agendamento novo, chamando
`confirm_appointment` — o que criou uma linha de `appointments` nova, sem
`booking_fee_paid_at`, sujeita a cobrança duplicada da taxa de reserva.

Um primeiro fix (já mergeado, PR #90) corrigiu o Guard 0 de `confirm_appointment`
para bloquear `pending_reschedule` independente da idade da data — isso impede a
criação da linha duplicada. Este spec cobre a segunda camada: por que a Eva não
tinha esse contexto para começar, e como fazer com que o fluxo correto
(`mark_reschedule_in_progress` → `reschedule_appointment`) seja tomado
proativamente, sem depender só do guard como rede de segurança.

## Causa raiz

`get_upcoming_appointments` (`app/database.py:319`), que monta o bloco "Consultas
agendadas" injetado no prompt da Eva a cada turno, tem o mesmo tipo de lacuna que o
Guard 0 tinha:

- `future_result`: inclui `pending_reschedule` só se `end_time >= now`
- `recent_result`: janela de 48h
- `unpaid_past_result`: só `status == "completed"` com saldo pendente

Um `pending_reschedule` com mais de 48h de idade não cai em nenhum dos três buckets
— fica **invisível** no prompt inteiro. Além disso, mesmo quando uma linha
`pending_reschedule` aparece hoje (dentro de `future`/`recent`), o texto não expõe o
`status` — só data, ID e tag de taxa — então fica indistinguível de uma consulta
`scheduled` normal, mesmo para quem lê o prompt.

## Mudanças

### 1. `app/database.py` — `get_upcoming_appointments`

Nova query, ao lado das três existentes: busca `appointments` com
`status == "pending_reschedule"`, `patient_id in patient_ids`, `end_time < cutoff_recent`
(evita duplicar linhas já cobertas por `future_result`/`recent_result`). Sem filtro de
idade — mesmo padrão do `unpaid_past_result`, que já ignora quão antiga é uma
consulta `completed` com saldo pendente.

Cada linha retornada por essa query ganha a flag `stale_reschedule=True` (paralelo a
`already_occurred`/`recently_ended`).

### 2. `app/graph/nodes.py` — montagem do prompt

No loop que já monta os labels de cada consulta (`patient_agent_node`, ~linha 1536):

- **Tag de status explícita**: se `apt.get("status") == "pending_reschedule"`,
  acrescenta `" 🔄 REMARCAÇÃO PENDENTE"` ao label — independente de qual bucket a
  linha cai (`future`, `recent` ou o novo `stale`). Isso cobre tanto o caso "invisível
  há mais de 48h" quanto o caso "visível mas indistinguível de uma consulta normal".
- **Novo bucket** `stale_reschedule_lines`, populado quando `apt.get("stale_reschedule")`
  é `True`. Cabeçalho próprio, fora do agrupamento "Consultas agendadas" (que sugere
  consulta futura confirmada):
  ```
  Remarcação pendente (vaga liberada, aguardando nova data):
  ```

### 3. `app/graph/prompts.py` — reforço da instrução

A instrução existente (linha ~498, "Quando o paciente com status pending_reschedule
quiser remarcar...") é reativa e está enterrada num bloco grande sobre política de
cancelamento/reagendamento. Adiciono uma regra mais direta, ancorada na tag nova,
como parágrafo próprio logo no início da seção de política de cancelamento/
reagendamento (antes do bloco "CONSEQUÊNCIAS", linha ~461) — para ser lida antes de
qualquer outra regra de remarcação, não depois:

> Se qualquer consulta estiver marcada com 🔄 REMARCAÇÃO PENDENTE e o paciente voltar
> a falar sobre marcar/agendar — mesmo que a mensagem pareça um pedido novo — trate
> SEMPRE como continuação da remarcação: chame `mark_reschedule_in_progress` (com o
> `appointment_id` dessa consulta) ANTES de `get_available_slots`, e finalize com
> `reschedule_appointment` — NUNCA `confirm_appointment`. Isso vale mesmo que a data
> original pareça antiga — o registro de remarcação pendente não expira.

## Testes

- `tests/test_database_shim.py`: novo teste garantindo que um `pending_reschedule`
  com `end_time` há mais de 48h aparece no retorno de `get_upcoming_appointments`
  com `stale_reschedule=True`, e que um `pending_reschedule` recente (dentro da
  janela de 48h) não é duplicado entre buckets.
- `tests/test_process_message.py`: novo teste garantindo que o prompt final contém
  a tag `🔄 REMARCAÇÃO PENDENTE` e o cabeçalho "Remarcação pendente" quando
  `get_upcoming_appointments` retorna uma linha com `stale_reschedule=True`.

## Fora de escopo

- Não se cria nenhum campo novo de estado de conversa (checkpoint) — a fonte de
  verdade continua sendo o banco, lido a cada turno via `get_upcoming_appointments`,
  igual ao padrão já usado para `already_occurred`/`recently_ended`.
- O Guard 0 de `confirm_appointment` (PR #90) permanece como está — esta mudança é
  complementar, não substitui a rede de segurança.
- Não se altera `release_pending_reschedules.py` nem a lógica de liberação de slot.
