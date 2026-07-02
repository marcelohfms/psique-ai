import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.graph.tools import _get_doctor_calendar_id
    from app.database import get_supabase

    client = await get_supabase()
    doc = await client.table("doctors").select("*").eq("doctor_id", "d5baa58b-a788-4f40-b8c0-512c189150be").execute()
    print("DOCTOR:", doc.data)

    calendar_id = doc.data[0]["agenda_id"]
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    ev = service.events().get(calendarId=calendar_id, eventId="oktcvjec759pjnu9vl2bfqje2g").execute()
    print("EVENT:", ev.get("summary"), "|", ev.get("description"), "|", ev.get("location"))

asyncio.run(main())
