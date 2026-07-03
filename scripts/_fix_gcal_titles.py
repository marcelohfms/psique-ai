import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.database import get_supabase

    client = await get_supabase()
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    fixes = [
        # (calendar_id, event_id, new_title)
        ("dr.juliogouveia@gmail.com", "l21846ehpa1ct30p34u37pnq0k", "Consulta — Vicente Ximênes Lopes Novaes Gonçalves [Presencial]"),
        ("brunalima.psiquiatra@gmail.com", "mi87btfgmui0kmp6ita6pkf5qo", "Consulta — Jonas Santos Ferreira [Presencial]"),
    ]

    for cal_id, event_id, new_title in fixes:
        event = service.events().get(calendarId=cal_id, eventId=event_id).execute()
        event["summary"] = new_title
        updated = service.events().update(calendarId=cal_id, eventId=event_id, body=event).execute()
        print("✅", updated.get("summary"))

asyncio.run(main())
