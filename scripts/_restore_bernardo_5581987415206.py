import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
APPT_ID = "af69bf72-57fb-49b6-94a5-ad7888d93de3"
PATIENT = "Bernardo Lima Beltrão Teixeira"

async def main():
    from app.database import get_supabase, log_event
    client = await get_supabase()

    appt = await client.from_("appointments").select("*").eq("id", APPT_ID).single().execute()
    print("Before:", appt.data["status"], appt.data.get("booking_fee_paid_at"))

    now = datetime.now(TZ).isoformat()
    # Comprovante foi enviado 31/07 14:27:29, registrar com essa data
    fee_paid_at = datetime(2026, 7, 31, 14, 27, 29, tzinfo=TZ).isoformat()

    r = await client.from_("appointments").update({
        "status": "scheduled",
        "booking_fee_paid_at": fee_paid_at,
        "payment_reminder_sent_at": None,  # Clear the reminder flag
        "updated_at": now,
    }).eq("id", APPT_ID).execute()

    print("After:", r.data[0]["status"], r.data[0].get("booking_fee_paid_at"))

    await log_event("appointment_restored_payment_bug", "5581987415206", {
        "patient": PATIENT,
        "appointment_id": APPT_ID,
        "issue": "Eva respondeu ao comprovante mas nao chamou register_payment (stage=collect_info, sem ferramentas). Auto-cancel acionado erroneamente. Restaurado manualmente.",
        "comprovante_data": "2026-07-31 14:27:29",
        "comprovante_valor": "100,00",
        "drive_link": "https://drive.google.com/file/d/1D6U40KMVf54MAjjHFOxZHd9ExQp_fiqG/view?usp=drivesdk",
    })
    print("✅ Consulta restaurada!")

asyncio.run(main())
