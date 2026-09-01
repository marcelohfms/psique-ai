import asyncio
from dotenv import load_dotenv
load_dotenv()
async def main():
    from app.supabase_client import get_supabase
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    c = await get_supabase()
    cal_id="dr.juliogouveia@gmail.com"
    service = build("calendar","v3",credentials=_credentials())
    for eid in ["uvujd8pjg6aha3a7h6rfegnvmc","ihaq6vut8fpls53f4nupeelcjc","4rubvrm1meegbv87k9ngphri48"]:
        try:
            ev = service.events().get(calendarId=cal_id, eventId=eid).execute()
            print(f"\n=== {eid} ===")
            print(f"  status={ev.get('status')} summary={ev.get('summary')!r}")
            print(f"  created={ev.get('created')} updated={ev.get('updated')} creator={ev.get('creator')} organizer={ev.get('organizer',{}).get('email')}")
            print(f"  start={ev.get('start',{}).get('dateTime')} end={ev.get('end',{}).get('dateTime')}")
            print(f"  desc={str(ev.get('description'))[:150]!r}")
        except Exception as e:
            print(f"\n=== {eid} === ERRO {e}")
asyncio.run(main())
