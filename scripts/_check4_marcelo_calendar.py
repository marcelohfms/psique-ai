import asyncio
from dotenv import load_dotenv
load_dotenv()

EVENT_ID = "dtkqjgk94201rec9nd9p9jnvng"
AGENDA = "dr.juliogouveia@gmail.com"


async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    service = build("calendar", "v3", credentials=_credentials())

    print("=== EVENTO pelo appointment_id ===")
    try:
        e = service.events().get(calendarId=AGENDA, eventId=EVENT_ID).execute()
        print(f"  status={e.get('status')}")
        print(f"  summary={e.get('summary')}")
        print(f"  start={e.get('start')}  end={e.get('end')}")
        print(f"  created={e.get('created')} updated={e.get('updated')}")
        print(f"  description={(e.get('description') or '')[:300]}")
    except Exception as ex:
        print("  erro:", ex)

    for day in ("2026-08-05", "2026-08-06", "2026-08-13"):
        print(f"\n=== AGENDA Dr. Júlio {day} (showDeleted=True) ===")
        tmin = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=TZ).isoformat()
        tmax = datetime.fromisoformat(f"{day}T23:59:59").replace(tzinfo=TZ).isoformat()
        res = service.events().list(
            calendarId=AGENDA, timeMin=tmin, timeMax=tmax,
            singleEvents=True, showDeleted=True, orderBy="startTime",
        ).execute()
        for e in res.get("items", []):
            s = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
            print(f"  [{e.get('status')}] {s} | {e.get('summary')} | id={e['id']}")


asyncio.run(main())
