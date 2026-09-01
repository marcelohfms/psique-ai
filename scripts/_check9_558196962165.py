import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT = "j74rkero61h40fjm0794gska2o"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id
    from app import google_calendar as gc
    client = await get_supabase()

    cal_id = await _get_doctor_calendar_id("julio")
    print("julio calendar_id:", cal_id)

    creds = gc._load_credentials() if hasattr(gc, "_load_credentials") else None
    from googleapiclient.discovery import build
    # reuse whatever creds builder exists
    import inspect
    print([n for n in dir(gc) if "cred" in n.lower() or "service" in n.lower()])

asyncio.run(main())
