"""
One-off: altera modalidade da consulta de Eduardo Lyra em 22/07/2026 para online.
Uso: uv run python scripts/_set_eduardo_lyra_modality_online.py
"""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PATIENT_NAME_QUERY = "%Eduardo Lyra%"
TARGET_DATE = date(2026, 7, 22)
NEW_MODALITY = "online"
TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase, DOCTOR_NAMES, DOCTOR_IDS
    from app.google_calendar import update_event

    client = await get_supabase()

    patients_result = await client.from_("patients").select(
        "id, name, doctor_id, email"
    ).ilike("name", PATIENT_NAME_QUERY).execute()

    if not patients_result.data:
        print(f"❌ Nenhum paciente encontrado para '{PATIENT_NAME_QUERY}'.")
        return

    if len(patients_result.data) > 1:
        print("⚠️  Mais de um paciente encontrado:")
        for p in patients_result.data:
            print(f"  id={p['id']} | name={p['name']}")
        print("Abortando — refine a busca.")
        return

    patient = patients_result.data[0]
    patient_id = patient["id"]
    doctor_key = DOCTOR_NAMES.get(patient.get("doctor_id", ""), "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
    print(f"Paciente: {patient['name']} (id={patient_id})")
    print(f"Médico:   {doctor_label}")

    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, modality, status"
    ).eq("patient_id", patient_id).order("start_time", desc=True).execute()

    if not appt_result.data:
        print("❌ Nenhuma consulta encontrada para este paciente.")
        return

    print("Consultas encontradas:")
    for a in appt_result.data:
        print(f"  {a['start_time']} | status={a['status']} | modality={a.get('modality')} | id={a['appointment_id']}")

    matches = [
        a for a in appt_result.data
        if datetime.fromisoformat(a["start_time"]).astimezone(TZ).date() == TARGET_DATE
    ]

    if not matches:
        print(f"❌ Nenhuma consulta encontrada em {TARGET_DATE.strftime('%d/%m/%Y')}.")
        return

    if len(matches) > 1:
        print(f"⚠️  Mais de uma consulta encontrada em {TARGET_DATE.strftime('%d/%m/%Y')}. Abortando.")
        return

    appt = matches[0]
    start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    end_dt = datetime.fromisoformat(appt["end_time"]).astimezone(TZ)
    slot_minutes = int((end_dt - start_dt).total_seconds() // 60) or 60
    print(f"Consulta:         {start_dt.strftime('%d/%m/%Y %H:%M')} (ID: {appt['appointment_id']})")
    print(f"Modalidade atual: {appt.get('modality', 'não definida')}")

    if appt.get("modality") == NEW_MODALITY:
        print(f"ℹ️  Já está como '{NEW_MODALITY}'. Nenhuma alteração necessária.")
        return

    doctor_id = DOCTOR_IDS.get(doctor_key)
    cal_result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
    calendar_id = cal_result.data.get("agenda_id") if cal_result.data else None

    if not calendar_id:
        print("❌ calendar_id não encontrado — abortando (evita inconsistência entre banco e agenda).")
        return

    try:
        await update_event(
            calendar_id=calendar_id,
            event_id=appt["appointment_id"],
            new_start=start_dt,
            slot_minutes=slot_minutes,
            patient_name=patient["name"],
            doctor_name=doctor_label,
            modality=NEW_MODALITY,
            patient_email=patient.get("email") or "",
        )
        print("✅ Evento no Google Calendar atualizado.")
    except Exception as e:
        print(f"❌ Falha ao atualizar Google Calendar: {e}")
        print("Abortando sem atualizar o banco (evita inconsistência).")
        return

    await client.from_("appointments").update({
        "modality": NEW_MODALITY,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appt["appointment_id"]).execute()
    print(f"✅ Modalidade atualizada para '{NEW_MODALITY}' no banco.")


if __name__ == "__main__":
    asyncio.run(main())
