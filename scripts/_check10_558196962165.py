import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT = "j74rkero61h40fjm0794gska2o"
CAL = "dr.juliogouveia@gmail.com"

async def main():
    from app import google_calendar as gc
    from googleapiclient.discovery import build
    creds = gc._credentials()
    service = build("calendar", "v3", credentials=creds)
    try:
        ev = service.events().get(calendarId=CAL, eventId=APPT).execute()
        print("STATUS:", ev.get("status"))
        print("SUMMARY:", ev.get("summary"))
        print("DESCRIPTION:", ev.get("description"))
        print("START:", ev.get("start"))
    except Exception as e:
        print("ERR:", e)

asyncio.run(main())
