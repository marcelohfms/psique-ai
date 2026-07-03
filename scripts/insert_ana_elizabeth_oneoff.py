"""
One-off: cadastra Ana Elizabeth Diniz de Barros e agenda para 16/06/2026 às 16:00 (Dr. Júlio).
Uso: uv run python scripts/insert_ana_elizabeth_oneoff.py
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
NUMBER = "5581998782468"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

PATIENT = {
    "number": NUMBER,
    "name": "Ana Elizabeth Diniz de Barros",
    "patient_name": "Ana Elizabeth Diniz de Barros",
    "birth_date": "24/03/1968",
    "age": 58,
    "email": "bethdinizdebarros@gmail.com",
    "patient_cpf": "509.282.604-53",
    "doctor_id": DOCTOR_ID_JULIO,
    "is_patient": True,
    "active": True,
}

APPOINTMENT_START = datetime(2026, 6, 16, 16, 0, tzinfo=TZ)
SLOT_MINUTES = 60


async def main():
    from app.database import get_supabase, get_users_by_phone
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import create_event

    client = await get_supabase()

    # --- Inserir/atualizar paciente ---
    existing = await get_users_by_phone(NUMBER)
    row = next(
        (r for r in existing if (r.get("patient_name") or "").lower() == "ana elizabeth diniz de barros"),
        None,
    )
    if row:
        await client.from_("users").update(PATIENT).eq("id", row["id"]).execute()
        user_id = row["id"]
        print(f"Atualizado: Ana Elizabeth (id={user_id})")
    else:
        result = await client.from_("users").insert(PATIENT).execute()
        inserted = (result.data or [{}])[0]
        user_id = inserted.get("id")
        print(f"Inserido: Ana Elizabeth (id={user_id})")

    # --- Criar evento no Google Calendar ---
    calendar_id = await _get_doctor_calendar_id("julio")
    print(f"Calendar ID: {calendar_id}")

    event_id = await create_event(
        calendar_id=calendar_id,
        start=APPOINTMENT_START,
        slot_minutes=SLOT_MINUTES,
        patient_name="Ana Elizabeth Diniz de Barros",
        doctor_name="Dr. Júlio",
        patient_email="bethdinizdebarros@gmail.com",
        patient_number=NUMBER,
    )
    print(f"Evento criado: {event_id}")

    # --- Inserir agendamento no banco ---
    end = APPOINTMENT_START + __import__("datetime").timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").insert({
        "user_id": user_id,
        "doctor_id": DOCTOR_ID_JULIO,
        "appointment_id": event_id,
        "start_time": APPOINTMENT_START.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "consultation_type": "primeira_consulta",
    }).execute()
    print(f"Agendamento salvo: 16/06/2026 às 16:00 — Ana Elizabeth Diniz de Barros")


asyncio.run(main())
