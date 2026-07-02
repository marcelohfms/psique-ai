import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_user_by_phone, DOCTOR_IDS
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    user = await get_user_by_phone("5581992933054")
    print("User:", user.get("name"), "| id:", user.get("id"))

    # Check if old appointment exists
    old_id = "82url5d7mmu25s469intfjgj5k"
    new_id = "stpsji3s37tmiahf8j4daltgr0"
    existing = await client.from_("appointments").select("id, appointment_id, start_time, status").eq("appointment_id", old_id).execute()
    print("Appt existente (old_id):", existing.data)

    if existing.data:
        # Update appointment_id to new calendar event
        r = await client.from_("appointments").update({"appointment_id": new_id}).eq("appointment_id", old_id).execute()
        print("appointment_id atualizado:", r.data[0]["appointment_id"])
    else:
        # Create new appointment
        start = datetime(2026, 7, 6, 9, 0, tzinfo=TZ)
        end   = datetime(2026, 7, 6, 11, 0, tzinfo=TZ)
        appt = {
            "appointment_id": new_id,
            "user_id": user["id"],
            "doctor_id": DOCTOR_IDS["julio"],
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": "presencial",
            "consultation_type": "primeira_consulta",
            "booking_fee_waived": False,
        }
        r = await client.from_("appointments").insert(appt).execute()
        print("Criado:", r.data[0]["appointment_id"])

asyncio.run(main())
