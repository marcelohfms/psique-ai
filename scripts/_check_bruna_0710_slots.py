import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, DOCTOR_IDS
    from app.google_calendar import get_available_slots

    client = await get_supabase()
    doctor_id = DOCTOR_IDS.get("bruna")
    result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
    calendar_id = result.data.get("agenda_id")
    print("calendar_id:", calendar_id)

    slots = await get_available_slots(calendar_id, "10/07", "manha", 60, doctor_key="bruna")
    for s, mod in slots:
        print(s, mod)

asyncio.run(main())
