import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581992717880"

async def main():
    from app.database import get_supabase
    from app.google_calendar import update_event
    from app.graph.tools import _get_doctor_calendar_id
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    contacts = await client.from_("contacts").select("id").eq("phone", PHONE).execute()
    contact_id = contacts.data[0]["id"]
    pcs = await client.from_("patient_contacts").select("patient_id").eq("contact_id", contact_id).execute()
    patient_ids = [pc["patient_id"] for pc in pcs.data]

    appt = await client.from_("appointments").select("*").in_("patient_id", patient_ids).eq("status", "scheduled").order("start_time").limit(1).execute()
    if not appt.data:
        print("Sem agendamento scheduled")
        return

    a = appt.data[0]
    print(f"Agendamento atual: {a['start_time']} | {a['appointment_id']} | {a['modality']}")

    new_start = datetime(2026, 6, 30, 14, 0, 0, tzinfo=TZ)
    new_end = new_start + timedelta(hours=1)

    calendar_id = await _get_doctor_calendar_id("julio")
    print(f"Atualizando Calendar event {a['appointment_id']} → {new_start}")
    await update_event(
        calendar_id=calendar_id,
        event_id=a["appointment_id"],
        new_start=new_start,
        slot_minutes=60,
        patient_name="Natiane Larissa Cajueiro Araújo",
        doctor_name="Dr. Júlio",
        is_minor_first=False,
        modality="online",
        patient_email="",
        patient_number=PHONE,
    )
    print("Calendar atualizado")

    await client.from_("appointments").update({
        "start_time": new_start.isoformat(),
        "end_time": new_end.isoformat(),
        "modality": "online",
        "updated_at": datetime.now(TZ).isoformat(),
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
    }).eq("appointment_id", a["appointment_id"]).execute()
    print("Banco atualizado — Natiane reagendada para 30/06 às 14h online ✅")

asyncio.run(main())
