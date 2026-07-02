"""
One-off: agenda primeira consulta de Paulo Victor Barros Lima Siqueira Diniz (menor, Dr. Júlio)
- 11/06/2026 às 15:00 — 1ª hora: responsáveis
- 11/06/2026 às 17:00 — 2ª hora: paciente
Uso: uv run python scripts/insert_paulovictor_appt_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581988521442@s.whatsapp.net"
USER_ID = "46de5fc6-fbce-46c7-b2e0-711d75babb04"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
PATIENT_NAME = "Paulo Victor Barros Lima Siqueira Diniz"
CONTACT_NAME = "Dr. Paulo Diniz"
DOCTOR_LABEL = "Dr. Júlio"

SLOTS = [
    {"start": datetime(2026, 6, 11, 15, 0, tzinfo=TZ), "note": "1ª hora — responsáveis"},
    {"start": datetime(2026, 6, 11, 17, 0, tzinfo=TZ), "note": "2ª hora — paciente"},
]


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event

    client = await get_supabase()

    # Reativa o contato
    await client.from_("users").update({"active": True, "deactivated_at": None}).eq("id", USER_ID).execute()
    print(f"✅ Contato reativado: {CONTACT_NAME}")

    calendar_id = await _get_doctor_calendar_id("julio")

    for slot in SLOTS:
        start = slot["start"]
        note = slot["note"]

        event_id = await create_event(
            calendar_id=calendar_id,
            start=start,
            slot_minutes=60,
            patient_name=PATIENT_NAME,
            doctor_name=DOCTOR_LABEL,
            session_note=note,
            patient_number=PHONE,
        )
        print(f"✅ Evento criado: {start.strftime('%H:%M')} ({note}) → {event_id}")

        end = start + timedelta(hours=1)
        await client.from_("appointments").insert({
            "user_id": USER_ID,
            "doctor_id": DOCTOR_ID_JULIO,
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "consultation_type": "primeira_consulta",
        }).execute()
        print(f"✅ Agendamento salvo: {start.strftime('%d/%m/%Y %H:%M')} — {note}")

    await _notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {PATIENT_NAME} (menor, 17 anos)\n"
        f"Data e horário: 11/06/2026 às 15:00 e 17:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"1ª hora (15:00): responsáveis\n"
        f"2ª hora (17:00): paciente\n"
        f"Contato: {CONTACT_NAME}",
        phone=PHONE,
        subject=f"Agendamento realizado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada.")


asyncio.run(main())
