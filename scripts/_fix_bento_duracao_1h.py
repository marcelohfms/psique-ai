import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
APPT="uvujd8pjg6aha3a7h6rfegnvmc"
CAL="dr.juliogouveia@gmail.com"
START=datetime(2026,9,2,10,0,tzinfo=TZ)
NEW_END_ISO="2026-09-02T11:00:00-03:00"

async def main():
    from app.supabase_client import get_supabase
    from app.google_calendar import update_event, _credentials
    from googleapiclient.discovery import build
    c = await get_supabase()

    a=(await c.from_("appointments").select("start_time,end_time,status,consultation_type").eq("appointment_id",APPT).maybe_single().execute()).data
    print("ANTES (DB):", a)
    svc=build("calendar","v3",credentials=_credentials())
    ev=svc.events().get(calendarId=CAL,eventId=APPT).execute()
    print("ANTES (Calendar):", ev["start"]["dateTime"], "->", ev["end"]["dateTime"], "| summary:", ev.get("summary"))

    # Google Calendar: reconstrói evento com 1h (10:00–11:00), preservando dados atuais
    await update_event(
        CAL, APPT, new_start=START, slot_minutes=60,
        patient_name="Bento Ramos Valença", doctor_name="Júlio",
        is_minor_first=False, modality="online",
        patient_email="sandro_valenca@hotmail.com", patient_number="5581995397978",
    )

    # Banco: end_time = 11:00
    await c.from_("appointments").update({
        "end_time": datetime.fromisoformat(NEW_END_ISO).astimezone(ZoneInfo("UTC")).isoformat(),
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id",APPT).execute()

    a2=(await c.from_("appointments").select("start_time,end_time,status,consultation_type").eq("appointment_id",APPT).maybe_single().execute()).data
    print("\nDEPOIS (DB):", a2)
    ev2=svc.events().get(calendarId=CAL,eventId=APPT).execute()
    print("DEPOIS (Calendar):", ev2["start"]["dateTime"], "->", ev2["end"]["dateTime"], "| summary:", ev2.get("summary"))
    print("DESC:\n"+ev2.get("description",""))

asyncio.run(main())
