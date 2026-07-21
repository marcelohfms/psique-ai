"""Fix Heitor's calendar event to correct time (10:00 Recife, not 13:00 UTC)."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.database import get_supabase
    from app.google_calendar import create_event, cancel_event

    client = await get_supabase()

    patient_id = "1ef19604-d0cb-4cca-991f-44b1753d0e7a"

    # Get current appointment data
    appts = await client.table("appointments").select("*").eq("patient_id", patient_id).execute()
    appt = appts.data[0]

    old_event_id = appt["appointment_id"]
    print(f"📋 Agendamento atual:")
    print(f"  ID BD: {appt['id']}")
    print(f"  ID Google Calendar (a ser removido): {old_event_id}")
    print(f"  Data BD: {appt['start_time']}")

    # Get patient and doctor info
    patient_data = await client.table("patients").select("*").eq("id", patient_id).single().execute()
    patient = patient_data.data

    doctor_id = patient["doctor_id"]
    doctors = await client.table("doctors").select("*").eq("doctor_id", doctor_id).execute()
    doctor = doctors.data[0]

    # Get contact for phone number
    contacts = await client.table("patient_contacts").select("contact_id").eq("patient_id", patient_id).eq("role", "agendamento").execute()
    if contacts.data:
        contact_id = contacts.data[0]["contact_id"]
        contact_data = await client.table("contacts").select("*").eq("id", contact_id).single().execute()
        phone = contact_data.data.get("phone", "")
    else:
        phone = ""

    # Parse time correctly: 13:00 UTC = 10:00 Recife
    # The database stores UTC time, so we need to convert it to Recife timezone
    start_utc = datetime.fromisoformat(str(appt["start_time"]).replace("+00:00", "+00:00"))
    start_recife = start_utc.astimezone(TZ)

    slot_minutes = int((datetime.fromisoformat(str(appt["end_time"]).replace("+00:00", "")) -
                       datetime.fromisoformat(str(appt["start_time"]).replace("+00:00", ""))).total_seconds() / 60)

    print(f"\n👤 Paciente: {patient['name']}")
    print(f"👨‍⚕️ Médico: {doctor['name']}")
    print(f"📅 Data/Hora correta: {start_recife.strftime('%d/%m/%Y às %H:%M')} (Recife)")
    print(f"📍 Modalidade: {appt['modality']}")

    calendar_id = doctor.get("agenda_id")

    # Remove old event
    print(f"\n🗑️  Removendo evento errado...")
    try:
        await cancel_event(calendar_id, old_event_id)
        print(f"✅ Evento antigo removido")
    except Exception as e:
        print(f"⚠️  Aviso ao remover evento antigo: {e}")

    # Create new event with correct time
    print(f"\n✨ Criando novo evento com horário correto...")
    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=start_recife,
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
        print(f"\n✨ Resumo final:")
        print(f"  Data/Hora: {start_recife.strftime('%d/%m/%Y às %H:%M')} (Recife)")
        print(f"  ID Google Calendar (novo): {new_event_id}")
        print(f"  Status: {result.data[0]['status']}")
        print(f"\n✨ Agendamento de Heitor corrigido com sucesso!")
    else:
        print(f"❌ Falha ao atualizar")

asyncio.run(main())
