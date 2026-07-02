import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials, _get_busy
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

    start = datetime(2026, 8, 20, 15, 0, 0, tzinfo=TZ)
    end = datetime(2026, 8, 20, 16, 0, 0, tzinfo=TZ)

    from googleapiclient.discovery import build as _build
    busy = _get_busy(service, calendar_id, start, end)
    print("BUSY 15h:", busy)

asyncio.run(main())
