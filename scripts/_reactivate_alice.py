import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
APPOINTMENT_ID = "abcmv3s0lbtgf8oe8cvp0bu1mg"
SLOT_START = datetime(2026, 6, 22, 10, 0, tzinfo=TZ)
SLOT_MINUTES = 60
DOCTOR_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
PATIENT_NAME = "Maria Alice Zacarias De Melo Costa"
PHONE = "5587981054742@s.whatsapp.net"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import get_available_slots, create_event

    client = await get_supabase()
    calendar_id = await _get_doctor_calendar_id("julio")

    # Verifica disponibilidade
    shift = "manhã"
    slots = await get_available_slots(calendar_id, "22/06", shift, SLOT_MINUTES, "julio")
    available = any(s.replace(tzinfo=TZ) == SLOT_START or s == SLOT_START for s, _ in slots)
    print(f"Slot 22/06 10:00 disponível: {available}")
    print(f"Slots disponíveis: {[(s.strftime('%H:%M'), m) for s, m in slots]}")

    if not available:
        print("❌ Slot ocupado — precisará agendar novo horário")
        return

    # Recria evento no Calendar
    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=SLOT_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name="Dr. Júlio",
        modality="presencial",
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {new_event_id}")

    # Reativa appointment
    now = datetime.now(TZ)
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "booking_fee_paid_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print(f"✅ Consulta reativada: 22/06/2026 10:00 | status=scheduled | booking_fee_paid_at={now.strftime('%d/%m %H:%M')}")

asyncio.run(main())
