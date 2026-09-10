# Lembretes por idade + contato próprio + quem agendou

Data: 2026-09-10
Projeto 1 de 2 (o projeto 2, privacidade da conversa para terceiros, tem desenho próprio depois).

## Problema

A mãe de uma paciente adulta (Beatriz Loyola, 20 anos) recebeu o lembrete de véspera
da consulta da filha. Investigando:

1. A regra "paciente adulto recebe só no próprio número" já existe em
   `get_reminder_contacts` (app/patients.py), mas depende do marcador `is_self`.
2. O marcador `is_self`/`relationship` é gravado **por role** (agendamento, consulta,
   financeiro) em `patient_contacts`. Essas linhas divergiram: na ficha da Beatriz a
   linha de consulta da mãe (Leni) estava `is_self=True`, então o cron tratou a mãe
   como se fosse a própria paciente.
3. O painel da Eva mostra/edita **uma** linha por par (a de `agendamento`), então o
   painel parecia certo enquanto o cron lia a linha de consulta (errada).

Auditoria: 48 pares (paciente, contato) com marcador inconsistente/contraditório.
Em 46 deles a linha de `agendamento` está íntegra e é a verdade; só 2 tinham o próprio
agendamento bagunçado, já resolvidos manualmente pela clínica (Fernando/Pollyanna → mãe;
Luísa → self).

## Regras de negócio (validadas com a clínica)

### Lembrete de consulta (véspera + dia)
- Paciente **adulto** (>=18) **com contato próprio** cadastrado → só o contato próprio.
  Pai/mãe/terceiro nunca recebem.
- Caso contrário (menor, ou adulto sem contato próprio) → o **contato que agendou**
  aquela consulta (`appointments.contact_id`).

### Lembrete de retorno (nudge para remarcar)
- Adulto com contato próprio → só o contato próprio.
- Menor → só os **responsáveis** (contatos com relação mãe/pai/tutor/avó/...),
  excluindo terceiro avulso (is_self=False e relação vazia).
- Adulto sem contato próprio → fallback: responsáveis se houver, senão o contato que
  agendou a última consulta.
- Menor sem nenhum responsável cadastrado (ex.: só o próprio contato, caso Luísa) →
  fallback: o contato próprio / quem agendou a última consulta.

### Cobrança da taxa de reserva (2h sem pagar)
- Sempre o **contato que agendou** aquela consulta (`appointments.contact_id`). É a
  transação dele. Serve adulto que agendou sozinho, terceiro que agendou por um adulto,
  e responsável de menor.

### Terceiros (não é o próprio paciente adulto, não é responsável de menor)
- Recebem só mensagens da transação que estão fazendo (confirmação do agendamento /
  remarcação / cancelamento; ou a mensagem daquele pagamento). Nunca lembrete de
  consulta/retorno. **A restrição de visibilidade na conversa (não poder consultar
  outras consultas do paciente) é o Projeto 2, fora deste escopo.**

## Fatos técnicos que viabilizam

- `appointments.contact_id` já grava quem agendou cada consulta. 100% preenchido nas
  53 consultas agendadas hoje (nenhum backfill necessário).
- A linha `agendamento` de `patient_contacts` nunca foi corrompida pelo bug do marcador;
  é a mesma que o painel exibe. Pode ser usada como verdade.

## Arquitetura (Opção A — contida, escolhida)

Mantém o schema de `patient_contacts` (linhas por role). Muda 3 coisas:

### Parte 1 — Seleção de destinatário (app/patients.py)

Novos helpers puros + funções de seleção. `is_self`/`relationship` passam a ser
lidos de forma consistente por par (depois da Parte 2 estão iguais em todas as roles).

- `_is_guardian_relationship(rel) -> bool`: True se a relação normalizada (sem acento,
  minúscula) for de responsável: {mãe, pai, tutor, tutora, responsável, avó, avô, tio,
  tia, irmã, irmão, padrasto, madrasta, ...}. `self`/vazio/None → False.
- Classificação de um contato do paciente:
  - **próprio**: `is_self=True`.
  - **responsável**: `is_self=False` e `_is_guardian_relationship(rel)`.
  - **terceiro avulso**: `is_self=False` e relação vazia/None.

Funções (todas com `include_inactive=True`, como hoje — lembrete transacional chega
mesmo com o bot pausado):

- `consultation_reminder_contacts(patient, appointment) -> list[contact]`
  - adulto & tem contato próprio → [próprios]
  - senão → [contato de `appointment.contact_id`]; se ausente/irresolúvel → todos os
    contatos vinculados (fallback de segurança, comportamento atual).
- `return_reminder_contacts(patient) -> list[contact]`
  - adulto & tem contato próprio → [próprios]
  - menor → [responsáveis]; se nenhum → [próprio/quem agendou a última consulta]
  - adulto sem próprio → [responsáveis] senão [quem agendou a última consulta]
- `payment_reminder_contacts(patient, appointment) -> list[contact]`
  - → [contato de `appointment.contact_id`]; fallback: contatos com role financeiro / todos.

`get_reminder_contacts` atual (usada pelos crons) é substituída/ajustada por essas.
Os templates `_terceiro` (send_return_reminders) continuam iguais: disparam quando o
nome do contato != nome do paciente, o que segue correto (responsável de menor recebe
a variante `_terceiro`).

Crons afetados:
- `scripts/send_appointment_reminders.py` — passa o appointment (com `contact_id`) para
  `consultation_reminder_contacts`. Adicionar `contact_id` no SELECT.
- `scripts/send_return_reminders.py` — usa `return_reminder_contacts`.
- `scripts/send_payment_reminders.py` — usa `payment_reminder_contacts` (já filtra por
  consulta não paga; passar o appointment).

### Parte 2 — Limpeza única do marcador (migração)

Script one-off `scripts/_migrate_marker_from_agendamento.py` (dry-run primeiro):
- Para cada par (paciente, contato) cujas linhas divergem, copiar `(is_self,
  relationship)` da linha `agendamento` para as linhas `consulta` e `financeiro`.
- Os 2 casos de agendamento contraditório já foram resolvidos manualmente na clínica
  (Fernando/Pollyanna=mãe/not-self; Luísa=self) — a propagação do agendamento os
  finaliza automaticamente.
- Imprime cada mudança; aplica só após conferência.

### Parte 3 — Painel grava consistente (dashboard/)

- `dashboard/attendant_db.py::update_link` (ou `attendant_routes.py::update_vinculo`):
  quando o update contém `is_self`/`relationship`, aplicar a **todas** as linhas do par
  (patient_id, contact_id), não só ao `pc_id` daquela role.
- Leitura determinística: `get_link` passa a preferir a linha `agendamento` (ordenar/
  filtrar por role) em vez de `rows[0]` sem ordem.
- Registro (`app/database.py`) já grava as 3 roles iguais no loop — manter.

### Parte 4 — Roles consulta/financeiro

Deixam de escolher destinatário. Ficam na tabela por ora. Remoção é passo futuro,
depois de estável. `agendamento` continua (resolução de paciente por telefone,
app/patients.py:388).

## Testes

- `tests/test_patients.py`: unidade das 3 funções de seleção
  - adulto com próprio + responsável corrompido is_self=True → só próprio (caso Beatriz)
  - adulto com próprio, terceiro agendou → consulta só o próprio; cobrança o terceiro
  - menor → consulta = quem agendou; retorno = só responsáveis (exclui terceiro avulso)
  - adulto sem próprio → fallback quem agendou
  - menor sem responsável → fallback próprio/quem agendou
- `tests/test_reminders.py`: cron de consulta manda pro conjunto novo
- retorno: menor → só responsáveis
- pagamento: → quem agendou
- dashboard (`dashboard/tests/`): update_vinculo propaga is_self/relationship a todas
  as roles do par
- migração: teste da função pura de decisão (propaga agendamento)

## Rollout

- Deploy do código + rodar a migração (dry-run → aplicar).
- Depois disso, lembretes da Beatriz e afins vão só pro número certo.
- O lembrete de hoje da Beatriz não dá pra desfazer; escopo é impedir daqui pra frente.

## Fora de escopo (Projeto 2)

Privacidade na conversa: impedir que um terceiro, falando com a Eva sobre um paciente
adulto, veja ou pergunte outras consultas/consultas futuras. Mexe nas ferramentas do
agente. Desenho próprio depois.
