"""
One-off script to create blocker events in a doctor's Google Calendar.
Run via: uv run python scripts/block_calendar_slots.py
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)

# ── Configure here ────────────────────────────────────────────────────────────
CALENDAR_ID = "brunalima.psiquiatra@gmail.com"

SLOTS = [
    ("2026-05-20", 11),
    ("2026-05-20", 14),
    ("2026-05-20", 17),
    ("2026-06-03", 11),
    ("2026-06-10", 11),
]
# ─────────────────────────────────────────────────────────────────────────────


def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    for date_str, hour in SLOTS:
        y, m, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:])
        start = datetime(y, m, d, hour, 0, tzinfo=TZ)
        end   = datetime(y, m, d, hour + 1, 0, tzinfo=TZ)
        event = {
            "summary": "🔒 Bloqueado",
            "description": "Horário bloqueado — não disponível para agendamento.",
            "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": end.isoformat(),   "timeZone": TIMEZONE},
        }
        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"  Bloqueado: {date_str} {hour:02d}h → {result['id']}")

    print(f"\nTotal: {len(SLOTS)} horário(s) bloqueado(s).")


if __name__ == "__main__":
    main()
