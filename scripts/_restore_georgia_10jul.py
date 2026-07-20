import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

APPOINTMENT_ID = "b049k49jq1f3h8kaic6fpsktrg"
PATIENT_ID = "6501e83e-7c1d-4a9c-9ff4-d6bcc83e33f8"
CONTACT_PHONE = "5581981118614"


async def main():
    from app.database import get_supabase, DOCTOR_IDS
    from app.google_calendar import create_event, TIMEZONE
    from zoneinfo import ZoneInfo

    client = await get_supabase()

    appt = await client.from_("appointments").select("*").eq("appointment_id", APPOINTMENT_ID).maybe_single().execute()
    if not appt.data:
        print("Agendamento não encontrado.")
        return
    if appt.data["status"] != "pending_reschedule":
        print(f"Status inesperado: {appt.data['status']} — abortando.")
        return

    tz = ZoneInfo(TIMEZONE)
    start = datetime.fromisoformat(appt.data["start_time"]).astimezone(tz)
    end = datetime.fromisoformat(appt.data["end_time"]).astimezone(tz)
    slot_minutes = int((end - start).total_seconds() / 60)
    modality = appt.data.get("modality") or "presencial"

    doctor_row = await client.from_("doctors").select("agenda_id").eq("doctor_id", DOCTOR_IDS["bruna"]).single().execute()
    calendar_id = doctor_row.data["agenda_id"]

    patient = await client.from_("patients").select("name, email").eq("id", PATIENT_ID).single().execute()
    patient_name = patient.data["name"]
    patient_email = patient.data.get("email") or ""

    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=start,
        slot_minutes=slot_minutes,
        patient_name=patient_name,
        doctor_name="Dra. Bruna",
        modality=modality,
        patient_email=patient_email,
        patient_number=CONTACT_PHONE,
    )
    print("Novo evento criado:", new_event_id)

    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "reschedule_requested_at": None,
        "updated_at": datetime.now(tz).isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()

    print(f"Agendamento restabelecido: {patient_name} — {start.strftime('%d/%m/%Y às %H:%M')} ({modality})")

asyncio.run(main())
