---
name: doctor-schedule-blocking
description: Use whenever asked to bloquear/liberar a agenda de um médico (Dr. Júlio / Dra. Bruna) em uma data específica, ou to block/unblock a doctor's calendar for a given day. Covers both the Google Calendar side (creating "🔒 Bloqueado" events) and the code side (SCHEDULE_EXCEPTIONS in app/google_calendar.py) so the Eva bot stops offering those slots.
---

# Bloqueio de agenda de médico (data específica)

Bloquear a agenda de um médico num dia tem duas partes independentes — **fazer as duas**, não só uma:

1. **Google Calendar** (efeito imediato, visual) — criar eventos "🔒 Bloqueado" nos horários da grade.
2. **Código** (`app/google_calendar.py`) — adicionar a data em `SCHEDULE_EXCEPTIONS` para que a Eva pare de oferecer esses horários (só vale após deploy).

## Regra mais importante: NÃO sobrescrever horários já agendados

Antes de criar qualquer evento de bloqueio, **sempre liste os eventos existentes no calendário do médico naquele dia** e pule os horários que já têm consulta marcada. Não criar bloqueio em cima de agendamento existente — isso é ruído desnecessário no calendário (o horário já está ocupado) e não é o objetivo do bloqueio.

```python
# listar eventos do dia antes de bloquear
events = service.events().list(
    calendarId=calendar_id, timeMin=start_of_day, timeMax=end_of_day,
    singleEvents=True, orderBy="startTime"
).execute().get("items", [])
```

Reporte ao usuário quais horários já tinham consulta marcada (e não foram bloqueados), para que ele decida se quer cancelar essas consultas separadamente.

## IDs dos calendários

- Dr. Júlio: `dr.juliogouveia@gmail.com`
- Dra. Bruna: `brunalima.psiquiatra@gmail.com`

## Grade de horários (para saber quais slots bloquear por dia da semana)

Ver `DOCTOR_SCHEDULES` em [app/google_calendar.py](../../../app/google_calendar.py) — cada médico tem janelas por `weekday` (0=Segunda). Gerar a lista de horas cheias dentro dessas janelas (ex.: janela `(9,0,12,0,...)` → horas 9, 10, 11).

## Passo 1 — criar eventos de bloqueio no Calendar

Um script one-off (não precisa reutilizar os antigos, criar um novo por bloqueio):

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)

def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    for calendar_id, y, m, d, hours in BLOCKS:  # hours = só as que NÃO têm consulta
        for hour in hours:
            start = datetime(y, m, d, hour, 0, tzinfo=TZ)
            end   = datetime(y, m, d, hour + 1, 0, tzinfo=TZ)
            event = {
                "summary": "🔒 Bloqueado",
                "description": "Horário bloqueado — não disponível para agendamento.",
                "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
                "end":   {"dateTime": end.isoformat(),   "timeZone": TIMEZONE},
            }
            result = service.events().insert(calendarId=calendar_id, body=event).execute()
            print(f"  Bloqueado: {calendar_id} {d:02d}/{m:02d} {hour:02d}:00 -> {result['id']}")
```

## Passo 2 — adicionar exceção no código

Em `SCHEDULE_EXCEPTIONS` (`app/google_calendar.py`), sob a chave do médico (`"julio"` ou `"bruna"`):

```python
"2026-07-06": [],  # Segunda: sem atendimento (bloqueado)
```

Lista vazia = médico não atende nesse dia inteiro. Se for bloqueio parcial (só alguns horários da grade), usar o formato de janelas normal excluindo o período bloqueado — ver exemplos já existentes no arquivo (ex. `"2026-06-29"` da Bruna).

Rodar `uv run pytest --tb=short` depois de editar, para garantir que nada quebrou.
