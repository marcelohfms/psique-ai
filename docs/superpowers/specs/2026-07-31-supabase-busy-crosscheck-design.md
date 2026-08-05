# Cruzar disponibilidade com `appointments` do Supabase

Data: 2026-07-31

## Problema

`get_available_slots` (app/google_calendar.py) determina disponibilidade consultando
apenas o Google Calendar, via `_get_busy` (`events().list()`). Nunca cruza com a tabela
`appointments` do Supabase.

Consequência: qualquer linha com `status="scheduled"` cujo evento no Calendar não exista
— deletado por engano, falha no `create_event`, linha criada por script one-off — vira um
"slot fantasma" que a Eva oferece e vende de novo.

**Caso real:** a consulta de Maria Clara Ramos Perecmanis (03/08/2026 17h, Dr. Júlio, taxa
paga em 18/05) teve os eventos deletados do Calendar em 28–29/07 durante um ajuste de
agenda. A linha em `appointments` seguiu `scheduled`. Em 30/07 a Eva ofereceu e vendeu o
mesmo 17h para outra paciente, que também pagou. Corrigido manualmente; a causa raiz
seguia aberta.

O mesmo furo existe em `confirm_appointment`: a Guard 1 (Supabase) só checa duplicidade do
**mesmo** paciente, e a Guard 2 (Calendar) sofre exatamente do problema do evento deletado.

## Solução

### 1. `_get_supabase_busy(doctor_key, window_start, window_end, calendar_busy)`

Nova coroutine em `app/google_calendar.py`.

Query:

```
appointments
  where doctor_id  == DOCTOR_IDS[doctor_key]
    and status     == "scheduled"
    and start_time <  window_end
    and end_time   >  window_start
```

`status == "scheduled"` é um `eq`, não um `in_` — `pending_reschedule` fica de fora por
construção. Nesse estado o slot é liberado de propósito e **não** deve bloquear.

Retorna a lista de faixas `(start, end)` para bloqueio.

**Divergência:** para cada linha, verifica se alguma faixa vinda do Calendar a cobre. Se
nenhuma cobrir, emite `logger.error("CALENDAR_DIVERGENCE ...")` com `appointment_id`,
médico e horário. A inconsistência em si é um bug que alguém precisa ver — bloquear em
silêncio esconderia o problema.

Sem `doctor_key` não há como identificar o médico: pula a checagem e mantém o
comportamento atual.

### 2. `get_available_slots`

Funde as faixas do Supabase em `busy_ranges`, junto com as do Calendar.

Falha na consulta ao Supabase → `logger.error` e segue só com o Calendar (fail-open,
mesmo tratamento já aplicado a `_get_busy`). Derrubar toda a oferta de horários por uma
falha de banco é pior que o risco residual.

### 3. `confirm_appointment` — Guard 1b (cross-paciente)

Em `app/graph/tools.py`, junto das guardas existentes: mesma query de sobreposição por
`doctor_id` + `status="scheduled"`, excluindo os `patient_id` do próprio paciente (a
Guard 1 já cobre o caso mesmo-paciente). Se bater, retorna a mesma instrução interna de
"este horário acabou de ser ocupado" que a Guard 2 usa.

Respeita `force_encaixe` — fica dentro do bloco `if not force_encaixe`.

## Testes

`tests/test_calendar.py`:
- slot fantasma (`scheduled` sem evento no Calendar) é bloqueado;
- `pending_reschedule` **não** bloqueia;
- divergência é logada como error;
- falha do Supabase → fail-open, slots do Calendar preservados.

`tests/test_tools.py`:
- Guard 1b barra confirmação em horário já ocupado por outro paciente do mesmo médico.
