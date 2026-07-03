"""
One-off: altera modalidade da próxima consulta agendada de 558195168383 para presencial.
Uso: uv run python scripts/update_modality_558195168383_oneoff.py
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PHONE = "558195168383@s.whatsapp.net"
NEW_MODALITY = "presencial"
TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase, get_user_by_phone, DOCTOR_NAMES
    from app.google_calendar import update_event

    user = await get_user_by_phone(PHONE)
    if not user:
        print(f"❌ Usuário não encontrado para {PHONE}")
        return

    patient_name = user.get("patient_name") or user.get("name", "Paciente")
    user_id = user["id"]
    doctor_key = DOCTOR_NAMES.get(user.get("doctor_id", ""), "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
    print(f"Paciente: {patient_name}")
    print(f"Médico:   {doctor_label}")

    client = await get_supabase()

    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, modality, status"
    ).eq("user_id", user_id).eq("status", "scheduled").order("start_time").limit(1).execute()

    if not appt_result.data:
        print("❌ Nenhuma consulta agendada encontrada.")
        return

    appt = appt_result.data[0]
    start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    print(f"Consulta:         {start_dt.strftime('%d/%m/%Y %H:%M')} (ID: {appt['appointment_id']})")
    print(f"Modalidade atual: {appt.get('modality', 'não definida')}")

    if appt.get("modality") == NEW_MODALITY:
        print(f"ℹ️  Já está como '{NEW_MODALITY}'. Nenhuma alteração necessária.")
        return

    # Atualiza no banco
    await client.from_("appointments").update({
        "modality": NEW_MODALITY,
    }).eq("appointment_id", appt["appointment_id"]).execute()
    print(f"✅ Modalidade atualizada para '{NEW_MODALITY}' no banco.")

    # Atualiza no Google Calendar
    calendar_event_id = appt["appointment_id"]  # appointment_id = Google Calendar event ID
    # Calcula slot_minutes a partir de start/end
    end_dt = datetime.fromisoformat(appt["end_time"]).astimezone(TZ)
    slot_minutes = int((end_dt - start_dt).total_seconds() // 60) or 60

    # Busca calendar_id da médica
    from app.database import DOCTOR_IDS
    doctor_id = DOCTOR_IDS.get(doctor_key)
    cal_result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
    calendar_id = cal_result.data.get("agenda_id") if cal_result.data else None

    if calendar_event_id and calendar_id:
        try:
            patient_number = PHONE
            await update_event(
                calendar_id=calendar_id,
                event_id=calendar_event_id,
                new_start=start_dt,
                slot_minutes=slot_minutes,
                patient_name=patient_name,
                doctor_name=doctor_label,
                modality=NEW_MODALITY,
                patient_number=patient_number,
            )
            print("✅ Evento no Google Calendar atualizado.")
        except Exception as e:
            print(f"⚠️  Falha ao atualizar Google Calendar: {e}")
    else:
        print("ℹ️  Sem calendar_event_id — Google Calendar não atualizado.")


if __name__ == "__main__":
    asyncio.run(main())
