import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

APPOINTMENT_ID = "v48mev55t4777kq84411bjtvo0"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event

    client = await get_supabase()
    calendar_id = await _get_doctor_calendar_id("julio")

    await cancel_event(calendar_id, APPOINTMENT_ID)
    print("✅ Evento removido do Google Calendar (11/06 14:00)")

    now = datetime.now(ZoneInfo("America/Recife"))
    await client.from_("appointments").update({
        "status": "canceled",
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Status atualizado para canceled no banco")

asyncio.run(main())
