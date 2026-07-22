import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    event = service.events().get(calendarId="brunalima.psiquiatra@gmail.com", eventId="faabnsrh5608t4k3ofs9fv2sps").execute()
    print(event.get("summary"), "|", event.get("start"), "|", event.get("status"))

asyncio.run(main())
