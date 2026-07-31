"""Recria o evento do bot no Google Calendar para a consulta do Bernardo.

O auto-cancel apagou o evento (events().delete) antes de o cancelamento ser
revertido no banco. Sem evento do bot, _get_busy não vê nada (os lançamentos
manuais da clínica em CAIXA ALTA são ignorados de propósito) e a Eva volta a
oferecer 15h e 16h de 13/08 para outro paciente.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
APPT_DB_ID = "af69bf72-57fb-49b6-94a5-ad7888d93de3"
PATIENT = "Bernardo Lima Beltrão Teixeira"
EMAIL = "rlabanatomia@gmail.com"
PHONE = "5581987415206"


async def main():
    from app.database import get_supabase, log_event
    from app.google_calendar import create_event, _credentials, _get_busy
    from googleapiclient.discovery import build

    client = await get_supabase()
    appt = (await client.from_("appointments").select("*").eq("id", APPT_DB_ID).single().execute()).data
    assert appt["status"] == "scheduled", appt["status"]

    start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    end = datetime.fromisoformat(appt["end_time"]).astimezone(TZ)
    slot_minutes = int((end - start).total_seconds() / 60)
    doc = (await client.from_("doctors").select("agenda_id").eq(
        "doctor_id", appt["doctor_id"]).single().execute()).data
    calendar_id = doc["agenda_id"]
    print(f"Slot: {start:%d/%m/%Y %H:%M}–{end:%H:%M} ({slot_minutes}min) em {calendar_id}")

    # Conflito com outro evento do bot? (lançamentos manuais da clínica não contam)
    svc = build("calendar", "v3", credentials=_credentials())
    loop = asyncio.get_running_loop()
    busy = await loop.run_in_executor(None, _get_busy, svc, calendar_id, start, end)
    if busy:
        print(f"❌ ABORTADO — já existe evento do bot nesse horário: {busy}")
        return

    old_event_id = appt["appointment_id"]
    new_event_id = await create_event(
        calendar_id, start, slot_minutes, PATIENT, "Júlio",
        is_minor_first=True,
        modality=appt.get("modality") or "presencial",
        patient_email=EMAIL, patient_number=PHONE,
    )
    print("Novo evento:", new_event_id)

    now_iso = datetime.now(TZ).isoformat()
    await client.from_("appointments").update({
        "appointment_id": new_event_id, "updated_at": now_iso,
    }).eq("id", APPT_DB_ID).execute()

    await log_event("appointment_calendar_event_recreated", PHONE, {
        "db_id": APPT_DB_ID, "old_event_id": old_event_id, "new_event_id": new_event_id,
        "reason": "evento apagado pelo auto-cancel indevido de 31/07/2026; consulta restaurada no banco mas o horário voltou a ser oferecido pela Eva",
    })
    print("✅ Evento recriado e appointment_id atualizado.")

asyncio.run(main())
