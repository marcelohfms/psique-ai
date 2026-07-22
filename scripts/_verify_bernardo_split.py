import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581991320003"
EVENT_ID = "lcko8pe0h2b0suggnfed54lclk"

M1 = datetime(2026, 7, 23, 19, 0, tzinfo=TZ)  # 1ª hora — responsáveis
M2 = datetime(2026, 7, 30, 15, 0, tzinfo=TZ)  # 2ª hora — paciente


async def main():
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import (
        _get_doctor_schedule, SCHEDULE_EXCEPTIONS, _credentials, _get_busy,
    )
    from googleapiclient.discovery import build
    from app.database import get_supabase, get_users_by_phone

    print("=== Weekdays ===")
    for label, dt in (("M1 23/07 19h", M1), ("M2 30/07 15h", M2)):
        print(f"  {label}: {dt.strftime('%A %d/%m/%Y %H:%M')} (weekday={dt.weekday()})")

    def in_schedule(dt):
        exc = SCHEDULE_EXCEPTIONS.get("julio", {})
        key = dt.date().isoformat()
        if key in exc:
            wins = exc[key]
        else:
            wins = _get_doctor_schedule("julio", dt.date()).get(dt.weekday(), [])
        m = dt.hour * 60 + dt.minute
        ok = any((sh*60+sm) <= m < (eh*60+em) for sh, sm, eh, em, _ in wins)
        return ok, wins

    print("\n=== Schedule check (Dr. Júlio) ===")
    for label, dt in (("M1 23/07 19h", M1), ("M2 30/07 15h", M2)):
        ok, wins = in_schedule(dt)
        print(f"  {label}: in_schedule={ok} | windows={wins}")

    cal_id = await _get_doctor_calendar_id("julio")
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    print("\n=== Busy check on Júlio calendar ===")
    for label, dt in (("M1 23/07 19-20", M1), ("M2 30/07 15-16", M2)):
        busy = _get_busy(service, cal_id, dt, dt + timedelta(hours=1))
        # ignore the patient's own current 2h event for M1
        conflicts = []
        for b in busy:
            conflicts.append((b.get("start"), b.get("end")))
        print(f"  {label}: busy_ranges={conflicts}")

    print("\n=== Current appointment row ===")
    users = await get_users_by_phone(PHONE + "@s.whatsapp.net")
    pids = [u["id"] for u in users]
    client = await get_supabase()
    appts = await client.from_("appointments").select("*").in_("patient_id", pids).order("start_time").execute()
    for a in appts.data:
        print(" ", a)


asyncio.run(main())
