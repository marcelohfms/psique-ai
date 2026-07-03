import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)
CALENDAR_ID = "dr.juliogouveia@gmail.com"

SLOTS = [(9, 0), (10, 0), (11, 0)]

def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    for hour, minute in SLOTS:
        start = datetime(2026, 6, 15, hour, minute, tzinfo=TZ)
        end   = datetime(2026, 6, 15, hour + 1, minute, tzinfo=TZ)
        event = {
            "summary": "🔒 Bloqueado",
            "description": "Horário bloqueado — não disponível para agendamento.",
            "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": end.isoformat(),   "timeZone": TIMEZONE},
        }
        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"  ✅ Bloqueado: 15/06 {hour:02d}:{minute:02d} → {result['id']}")

main()
