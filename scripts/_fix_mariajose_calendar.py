import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
EVENT_ID = "rj02m9ar2ffi1t181tluuk293k"
START = datetime(2026, 6, 17, 9, 0, tzinfo=TZ)

async def main():
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import update_event

    calendar_id = await _get_doctor_calendar_id("bruna")
    await update_event(
        calendar_id=calendar_id,
        event_id=EVENT_ID,
        new_start=START,
        slot_minutes=60,
        patient_name="Maria José Alves de Farias",
        doctor_name="Dra. Bruna",
        modality="presencial",
        patient_number="5581981139373@s.whatsapp.net",
    )
    print("✅ Evento atualizado no Calendar: Maria José Alves de Farias")

asyncio.run(main())
