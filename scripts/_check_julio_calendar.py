import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    import asyncio as _asyncio

    calendar_id = await _get_doctor_calendar_id("julio")

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    # Busca eventos de hoje entre 14h e 18h
    time_min = datetime(2026, 6, 11, 14, 0, tzinfo=TZ).isoformat()
    time_max = datetime(2026, 6, 11, 18, 0, tzinfo=TZ).isoformat()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute())

    events = result.get("items", [])
    print(f"Eventos Dr. Júlio hoje 14h–18h ({len(events)}):\n")
    for e in events:
        eid = e["id"]
        summary = e.get("summary", "sem título")
        start = e.get("start", {}).get("dateTime", "")
        start_fmt = datetime.fromisoformat(start).astimezone(TZ).strftime("%H:%M") if start else "?"
        created = e.get("created", "")
        creator = e.get("creator", {}).get("email", "?")
        description = (e.get("description") or "")[:150].replace("\n", " | ")
        print(f"  {start_fmt} | id={eid}")
        print(f"         summary: {summary}")
        print(f"         creator: {creator} | created: {created[:16]}")
        print(f"         desc:    {description}")
        print()

asyncio.run(main())
