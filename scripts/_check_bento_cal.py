import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.supabase_client import get_supabase
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    c = await get_supabase()
    d = await c.from_("doctors").select("agenda_id,name").eq("doctor_id","d5baa58b-a788-4f40-b8c0-512c189150be").single().execute()
    cal_id = d.data["agenda_id"]
    print("Julio calendar:", cal_id, d.data.get("name"))
    service = build("calendar","v3",credentials=_credentials())
    for appt in ["uvujd8pjg6aha3a7h6rfegnvmc","9f5dq513l813tk65i02te3vj88"]:
        try:
            ev = service.events().get(calendarId=cal_id, eventId=appt).execute()
            print(f"\nEVENT {appt}: status={ev.get('status')} summary={ev.get('summary')!r}")
            print("  start:", ev.get('start'), "end:", ev.get('end'))
        except Exception as e:
            print(f"\nEVENT {appt}: ERRO {e}")
    # Also list events on 02/09 to see what Julio actually has
    print("\n--- Eventos de Julio em 02/09 ---")
    r = service.events().list(calendarId=cal_id, timeMin="2026-09-02T00:00:00-03:00",
                              timeMax="2026-09-02T23:59:00-03:00", singleEvents=True, showDeleted=True).execute()
    for ev in r.get("items", []):
        print(f"  [{ev.get('status')}] {ev.get('start',{}).get('dateTime')} {ev.get('summary')!r} id={ev.get('id')}")

asyncio.run(main())
