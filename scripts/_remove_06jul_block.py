import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.graph.tools import _get_doctor_calendar_id

    calendar_id = await _get_doctor_calendar_id("julio")
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    for event_id in ["g2hisk66vgs9g9n7raugi4f9qc", "luvosogqd6lo27sl46a28sgv10"]:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        print(f"Removido: {event_id}")

asyncio.run(main())
