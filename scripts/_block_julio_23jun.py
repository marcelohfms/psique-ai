from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)
CALENDAR_ID = "dr.juliogouveia@gmail.com"

# Terça 23/06: grade 13h-18h → bloqueia 13, 14, 15, 16, 17
SLOTS = [13, 14, 15, 16, 17]

def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    for hour in SLOTS:
        start = datetime(2026, 6, 23, hour, 0, tzinfo=TZ)
        end   = datetime(2026, 6, 23, hour + 1, 0, tzinfo=TZ)
        event = {
            "summary": "🔒 Bloqueado",
            "description": "Horário bloqueado — não disponível para agendamento.",
            "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": end.isoformat(),   "timeZone": TIMEZONE},
        }
        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"  ✅ Bloqueado: 23/06 {hour:02d}:00 → {result['id']}")

main()
