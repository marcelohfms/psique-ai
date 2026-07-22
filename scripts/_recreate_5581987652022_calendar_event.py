"""Recreate calendar event for Heitor (5581987652022) following bot standard."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.database import get_supabase
    from app.google_calendar import create_event

    client = await get_supabase()

    patient_id = "1ef19604-d0cb-4cca-991f-44b1753d0e7a"

    # Get current appointment data
    appts = await client.table("appointments").select("*").eq("patient_id", patient_id).execute()
    appt = appts.data[0]

    old_event_id = appt["appointment_id"]
    print(f"📋 Agendamento atual:")
    print(f"  ID BD: {appt['id']}")
    print(f"  ID Google Calendar (antigo): {old_event_id}")
    print(f"  Data: {appt['start_time']}")
    print(f"  Status: {appt['status']}")

    # Get patient and doctor info
    patient_data = await client.table("patients").select("*").eq("id", patient_id).single().execute()
    patient = patient_data.data

    doctor_id = patient["doctor_id"]
    doctors = await client.table("doctors").select("*").eq("doctor_id", doctor_id).execute()
    doctor = doctors.data[0]

    print(f"\n👤 Paciente: {patient['name']}")
    print(f"👨‍⚕️ Médico: {doctor['name']}")

    # Get contact for phone number
    contacts = await client.table("patient_contacts").select("contact_id").eq("patient_id", patient_id).eq("role", "agendamento").execute()
    if contacts.data:
        contact_id = contacts.data[0]["contact_id"]
        contact_data = await client.table("contacts").select("*").eq("id", contact_id).single().execute()
        phone = contact_data.data.get("phone", "")
    else:
        phone = ""

    # Create new event
    start_dt = datetime.fromisoformat(str(appt["start_time"]).replace("+00:00", "")).replace(tzinfo=TZ)
    slot_minutes = int((datetime.fromisoformat(str(appt["end_time"]).replace("+00:00", "")) -
                       datetime.fromisoformat(str(appt["start_time"]).replace("+00:00", ""))).total_seconds() / 60)

    print(f"\n✨ Criando novo evento no Google Calendar...")
    print(f"  Data/Hora: {start_dt.strftime('%d/%m/%Y às %H:%M')}")
    print(f"  Duração: {slot_minutes} minutos")
    print(f"  Modalidade: {appt['modality']}")

    # Get calendar ID from doctors table
    calendar_id = doctor.get("agenda_id")
    if not calendar_id:
        print(f"❌ Erro: Calendário Google não configurado para {doctor['name']}")
        return

    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=start_dt,
        slot_minutes=slot_minutes,
        patient_name=patient["name"],
        doctor_name=doctor["name"],
        modality=appt["modality"],
        patient_email=patient.get("email", ""),
        patient_number=phone,
    )

    print(f"✅ Novo evento criado: {new_event_id}")

    # Update appointment record
    print(f"\n🔄 Atualizando registro no banco de dados...")
    result = await client.table("appointments").update({
        "appointment_id": new_event_id,
        "updated_at": datetime.now(TZ).isoformat()
    }).eq("id", appt["id"]).execute()

    if result.data:
        print(f"✅ Registro atualizado!")
        print(f"\n📊 Resumo:")
        print(f"  ID BD: {result.data[0]['id']}")
        print(f"  ID Google Calendar (novo): {new_event_id}")
        print(f"  Status: {result.data[0]['status']}")
        print(f"\n✨ Agendamento de Heitor restaurado com sucesso!")
    else:
        print(f"❌ Falha ao atualizar")

asyncio.run(main())
