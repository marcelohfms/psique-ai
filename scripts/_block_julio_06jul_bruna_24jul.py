from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)

# Segunda 06/07 (Júlio): grade 9-12 + 14-18 → bloqueia 9, 10, 11, 14, 15, 16, 17
# Sexta 24/07 (Bruna): grade 8-12 + 13-16 → bloqueia 8, 9, 10, 11, 13, 14, 15
BLOCKS = [
    ("dr.juliogouveia@gmail.com", 2026, 7, 6, [9, 10, 11, 14, 15, 16, 17]),
    ("brunalima.psiquiatra@gmail.com", 2026, 7, 24, [8, 9, 10, 11, 13, 14, 15]),
]

def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    for calendar_id, y, m, d, hours in BLOCKS:
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
            print(f"  ✅ Bloqueado: {calendar_id} {d:02d}/{m:02d} {hour:02d}:00 → {result['id']}")

if __name__ == "__main__":
    main()
