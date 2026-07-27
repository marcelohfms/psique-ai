"""
One-off: bloqueia agenda do Dr. Júlio em:
- 20/08 às 18h e 19h
- 31/08 tarde inteira (14h-18h)
- 15/10 a 27/10, todos os horários (só dias em que ele atende: seg/qua/qui)
- 28/10 a 30/10, todos os horários (idem)
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)
CALENDAR_ID = "dr.juliogouveia@gmail.com"

# (date_str, [hours])
BLOCKS = [
    ("2026-08-20", [18, 19]),
    ("2026-08-31", [14, 15, 16, 17]),
    ("2026-10-15", [9, 10, 11, 14, 15, 16, 17, 18, 19]),  # Quinta
    ("2026-10-19", [9, 10, 11, 14, 15, 16, 17]),           # Segunda
    ("2026-10-21", [9, 10, 11]),                            # Quarta
    ("2026-10-22", [9, 10, 11, 14, 15, 16, 17, 18, 19]),  # Quinta
    ("2026-10-26", [9, 10, 11, 14, 15, 16, 17]),           # Segunda
    ("2026-10-28", [9, 10, 11]),                            # Quarta
    ("2026-10-29", [9, 10, 11, 14, 15, 16, 17, 18, 19]),  # Quinta
]


def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    total = 0
    for date_str, hours in BLOCKS:
        y, m, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:])
        for hour in hours:
            start = datetime(y, m, d, hour, 0, tzinfo=TZ)
            end = datetime(y, m, d, hour + 1, 0, tzinfo=TZ)
            event = {
                "summary": "🔒 Bloqueado",
                "description": "Horário bloqueado — não disponível para agendamento.",
                "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
                "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
            }
            result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            print(f"  Bloqueado: {date_str} {hour:02d}h -> {result['id']}")
            total += 1
    print(f"\nTotal: {total} horário(s) bloqueado(s).")


if __name__ == "__main__":
    main()
