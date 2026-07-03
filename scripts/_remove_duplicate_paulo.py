import asyncio
from dotenv import load_dotenv
load_dotenv()

ORPHAN_EVENT_ID = "gt3crmgf8qv1usoq14crlba5mo"

async def main():
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event

    calendar_id = await _get_doctor_calendar_id("julio")
    await cancel_event(calendar_id, ORPHAN_EVENT_ID)
    print(f"✅ Evento órfão removido: {ORPHAN_EVENT_ID}")

asyncio.run(main())
