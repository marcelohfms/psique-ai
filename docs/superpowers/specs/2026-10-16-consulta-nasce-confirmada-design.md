# Consulta marcada na véspera ou no mesmo dia nasce confirmada

Data: 2026-10-16

## Problema

O lembrete de véspera ("Olá! Lembrete da Psiquê: Fulano tem consulta amanhã com
Dr. X às Hh. Consegue confirmar a presença?") serve para confirmar a presença de
quem marcou com antecedência. Quando o paciente marca a consulta na própria
véspera ou no mesmo dia, ele acabou de interagir com a clínica: a presença já
está, na prática, confirmada. Mandar esse lembrete é redundante.

Hoje a consulta da véspera em `scripts/send_appointment_reminders.py` só olha:

- `status == "scheduled"`
- `reminder_day_before_sent_at IS NULL`
- `start_time` cai no dia de amanhã (Recife)
- `created_at <= agora - 12h`

Ela não olha a relação entre o dia da marcação e o dia da consulta, e não olha se
a consulta já está confirmada. Resultado: uma consulta marcada pertinho da data
pode receber o lembrete de confirmação mesmo quando isso não faz sentido, e ela
não aparece confirmada (verde) na agenda nem no painel.

## Regra de negócio

No momento em que uma consulta ganha um horário — criação nova ou remarcação —
compara-se a data da consulta com a data de hoje, no fuso de Recife
(`America/Recife`):

```
days_ahead = (start.astimezone(TZ).date() - now.astimezone(TZ).date()).days
nasce_confirmada = 0 <= days_ahead <= 1
```

- `days_ahead == 0` (mesmo dia) → nasce confirmada
- `days_ahead == 1` (véspera) → nasce confirmada
- `days_ahead >= 2` (dois dias ou mais) → fluxo normal, recebe o lembrete
- `days_ahead < 0` (data no passado) → NÃO nasce confirmada

A fronteira superior (`<= 1`) é a mesma que `confirm_attendance` já usa para só
deixar o paciente confirmar a partir da véspera (lá, `days_ahead >= 2` bloqueia).
A fronteira inferior (`>= 0`) exclui datas no passado: uma data que já passou não
é véspera nem mesmo dia, então não deve nascer confirmada (importa para
remarcações e reativações, onde a nova data poderia, por erro, cair no passado).

"Nascer confirmada" significa confirmar de verdade, com os três efeitos que
`confirm_attendance` já produz hoje:

1. Grava `confirmed_at = agora` na linha da consulta.
2. Pinta o evento do Google Calendar de verde com ✅ (`mark_event_confirmed`).
3. Registra o evento `appointment_confirmed` (com `metadata.auto = true`, para a
   auditoria distinguir da confirmação feita pelo paciente).

## Arquitetura

### Predicado puro

Nova função em `app/dates.py` (que já define `TZ`):

```python
def is_born_confirmed(start: datetime, now: datetime) -> bool:
    """True quando a consulta está a 1 dia ou menos (véspera ou mesmo dia)."""
    days_ahead = (start.astimezone(TZ).date() - now.astimezone(TZ).date()).days
    return days_ahead <= 1
```

Pura, sem I/O, testável isolada.

### Helper de confirmação compartilhado

Os três efeitos (calendário verde + log do evento) já existem dentro de
`confirm_attendance` em `app/graph/tools.py`. Extrair um helper assíncrono
reaproveitado por `confirm_attendance` e pelos caminhos de marcação, para não
duplicar a lógica:

```python
async def _apply_auto_confirmation(calendar_id, event_id, phone):
    # pinta o evento de verde + ✅
    await mark_event_confirmed(calendar_id, event_id)
    await log_event("appointment_confirmed", phone,
                    {"appointment_id": event_id, "auto": True})
```

Observações:

- `confirmed_at` é gravado direto no `insert`/`update` de cada caminho (não num
  UPDATE extra), então o helper cuida apenas de calendário + log.
- Falha ao pintar o calendário não deve derrubar a marcação; segue o mesmo tom
  de "fire-and-forget" que o resto do código usa para o calendário.

## Caminhos afetados

Todos os caminhos que escrevem `start_time` numa consulta.

### 1. Marcação pela Eva — `confirm_appointment` (`app/graph/tools.py:1073`)

- Calcular `nasce = is_born_confirmed(start, datetime.now(TZ))`.
- Incluir `"confirmed_at": now.isoformat()` no dict do `insert` quando `nasce`.
- Depois do insert bem-sucedido, se `nasce`, chamar `_apply_auto_confirmation`.

### 2. Marcação pelo painel — `create_appointments` (`app/panel_booking.py:230`)

- Avaliar por parte (menor com 2 blocos avalia cada bloco pelo seu `start`).
- Incluir `confirmed_at` na linha quando a parte nasce confirmada.
- Depois do insert, aplicar o helper para cada parte que nasceu confirmada.

### 3. Remarcação — `reschedule_appointment` (`app/graph/tools.py:2475`)

Hoje o `reschedule_update` sempre faz `"confirmed_at": None`. Passa a:

- `"confirmed_at": now.isoformat() if is_born_confirmed(new_start, now) else None`.
- Depois da escrita e do tratamento do evento (criar novo ou mover em lugar),
  se a nova data nasce confirmada, chamar `_apply_auto_confirmation` sobre o id
  de evento vigente (o novo, no caso pending; o mesmo, no caso de move).

### 4. Primos da remarcação (fechando a brecha)

Como o helper é compartilhado, aplicar a mesma regra em:

- `keep_original_appointment` (`app/graph/tools.py:2206`) — ao reverter para a
  data original, se ela estiver a 1 dia ou menos, nasce confirmada.
- Remarcação interna dentro de `register_payment` (`app/graph/tools.py:~4442`) —
  mesmo tratamento ao recriar o evento.

### 5. Lembrete de véspera — `scripts/send_appointment_reminders.py`

Adicionar à query da véspera o filtro `confirmed_at IS NULL`
(`.is_("confirmed_at", "null")`). Com isso:

- Toda consulta que nasceu confirmada é pulada automaticamente.
- Qualquer consulta já confirmada por outro caminho também para de ser cobrada.

O filtro de 12h (`created_at <= agora - 12h`) permanece: protege o caso de uma
marcação feita pouco antes do envio das 7h para uma consulta ainda a 2+ dias.

## Fora de escopo

- O lembrete do mesmo dia (`lembrete_dia_consulta`) continua como está. É um
  aviso informativo, não um pedido de confirmação; o usuário pediu só o de
  véspera ("consulta amanhã").
- Nenhuma mudança na regra de 12h nem na janela de horário de envio (7h–12h).

## Testes

Arquivos existentes, uma camada por arquivo (`test_tools`, `test_calendar`,
mais o que cobre o script de lembretes).

- **Predicado** (`is_born_confirmed`): fronteira em 0, 1 e 2 dias; caso de
  virada de dia perto da meia-noite no fuso de Recife.
- **`confirm_appointment`** (mockado): grava `confirmed_at` e chama
  `mark_event_confirmed` + `log_event("appointment_confirmed", auto=True)`
  quando véspera/mesmo dia; NÃO grava nem pinta quando a 2+ dias.
- **`reschedule_appointment`** (mockado): nova data na véspera → `confirmed_at`
  preenchido e calendário pintado; nova data longe → `confirmed_at` zerado como
  hoje.
- **Lembrete de véspera**: consulta com `confirmed_at` preenchido não entra na
  lista de envio (a query inclui o filtro `confirmed_at IS NULL`).

Todos os testes devem passar no CI (`.github/workflows/test.yml`).
