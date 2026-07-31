"""A consulta voltou para scheduled no banco, mas o evento do bot no Google
Calendar foi apagado pelo auto-cancel. Como _get_busy só considera eventos do bot
(summary "Consulta..." / source=psique-bot) e ignora os lançamentos manuais em
CAIXA ALTA da clínica, o horário pode estar sendo oferecido a outro paciente."""
import asyncio
from datetime import date
from dotenv import load_dotenv
load_dotenv()

APPT_EVENT_ID = "7s6kh1vbhl2f15qb876scidqvc"


async def main():
    from app.database import get_supabase
    from app.google_calendar import get_available_slots

    client = await get_supabase()
    appt = await client.from_("appointments").select("*").eq(
        "appointment_id", APPT_EVENT_ID).single().execute()
    print("DB:", appt.data["status"], "| fee:", appt.data["booking_fee_paid_at"],
          "| event:", appt.data["appointment_id"])

    doc = await client.from_("doctors").select("agenda_id").eq(
        "doctor_id", appt.data["doctor_id"]).single().execute()
    slots = await get_available_slots(
        doc.data["agenda_id"], "13/08/2026", "", slot_minutes=60, doctor_key="julio")
    print("\nSlots que a Eva oferece em 13/08 para Dr. Júlio:")
    print(slots)

asyncio.run(main())
