import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()
    doc = await client.table("doctors").select("agenda_id").eq("doctor_id", "d5baa58b-a788-4f40-b8c0-512c189150be").single().execute()
    calendar_id = doc.data["agenda_id"]

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    start = datetime(2026, 8, 20, 13, 0, 0, tzinfo=TZ).isoformat()
    end = datetime(2026, 8, 20, 20, 0, 0, tzinfo=TZ).isoformat()

    events = service.events().list(calendarId=calendar_id, timeMin=start, timeMax=end, singleEvents=True).execute()
    for e in events.get("items", []):
        print(e.get("id"), "|", e.get("summary"), "|", e.get("start"), "->", e.get("end"))

asyncio.run(main())
