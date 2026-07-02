import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.database import get_supabase

    client = await get_supabase()
    doc = await client.table("doctors").select("agenda_id").eq("doctor_id", "d5baa58b-a788-4f40-b8c0-512c189150be").single().execute()
    calendar_id = doc.data["agenda_id"]

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    event = service.events().get(calendarId=calendar_id, eventId="oktcvjec759pjnu9vl2bfqje2g").execute()
    event["summary"] = "Consulta — Martin Trajano Vieira"
    updated = service.events().update(calendarId=calendar_id, eventId="oktcvjec759pjnu9vl2bfqje2g", body=event).execute()
    print("✅ Título atualizado:", updated.get("summary"))

asyncio.run(main())
