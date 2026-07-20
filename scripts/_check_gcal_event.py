import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build

    client = await get_supabase()
    doc = await client.from_("doctors").select("agenda_id").eq("doctor_id", "18b01f87-eacd-4905-bd4a-a8293991e6fd").single().execute()
    calendar_id = doc.data["agenda_id"]
    print("calendar_id:", calendar_id)

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    try:
        event = service.events().get(calendarId=calendar_id, eventId="bb3psfo966q5vqhq0kc4bifpms").execute()
        print("EVENTO ENCONTRADO:")
        print(event.get("summary"), event.get("start"), event.get("status"))
    except Exception as e:
        print("ERRO ao buscar evento:", e)

asyncio.run(main())
