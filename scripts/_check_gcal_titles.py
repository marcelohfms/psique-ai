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
    docs = await client.table("doctors").select("name, agenda_id").execute()
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(TZ).isoformat()

    for doc in docs.data:
        calendar_id = doc["agenda_id"]
        doctor_name = doc["name"]
        print(f"\n=== Dr(a). {doctor_name} ===")

        page_token = None
        while True:
            events = service.events().list(
                calendarId=calendar_id,
                timeMin=now,
                singleEvents=True,
                orderBy="startTime",
                privateExtendedProperty="source=psique-bot",
                pageToken=page_token,
            ).execute()
            for e in events.get("items", []):
                summary = e.get("summary", "")
                start = e.get("start", {}).get("dateTime", "")
                dt = datetime.fromisoformat(start).strftime("%d/%m/%Y %H:%M") if start else "?"
                has_modality = "[Online]" in summary or "[Presencial]" in summary
                if not has_modality:
                    print(f"  ⚠️  {dt} | {summary} | id={e['id']}")
            page_token = events.get("nextPageToken")
            if not page_token:
                break

asyncio.run(main())
