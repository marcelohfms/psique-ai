import asyncio
from dotenv import load_dotenv
load_dotenv()

APPOINTMENT_ID = "ciet2g7cq5q7bbn2va9g4gs6mo"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event
    from datetime import datetime
    from zoneinfo import ZoneInfo

    client = await get_supabase()

    # Cancela no Google Calendar
    calendar_id = await _get_doctor_calendar_id("julio")
    await cancel_event(calendar_id, APPOINTMENT_ID)
    print("✅ Evento removido do Google Calendar (25/06 19:00)")

    # Cancela no banco
    now = datetime.now(ZoneInfo("America/Recife"))
    await client.from_("appointments").update({
        "status": "canceled",
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Status atualizado para canceled no banco")

asyncio.run(main())
