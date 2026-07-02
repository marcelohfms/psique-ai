import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.graph.tools import _get_doctor_calendar_id
    from datetime import datetime
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    calendar_id = await _get_doctor_calendar_id("julio")
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    start = datetime(2026, 7, 6, 0, 0, 0, tzinfo=TZ).isoformat()
    end = datetime(2026, 7, 7, 0, 0, 0, tzinfo=TZ).isoformat()

    events = service.events().list(calendarId=calendar_id, timeMin=start, timeMax=end, singleEvents=True).execute()
    for e in events.get("items", []):
        print(e.get("id"), "|", e.get("summary"), "|", e.get("start"), "->", e.get("end"))

asyncio.run(main())
