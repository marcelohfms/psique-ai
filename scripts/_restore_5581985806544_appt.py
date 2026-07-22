import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, log_event
    from app.google_calendar import create_event
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    db_id = "29532a6d-1b91-4080-a1b7-67ab1f6de3de"
    calendar_id = "brunalima.psiquiatra@gmail.com"
    patient_id = "f1aa585d-4a0d-42b3-908e-14a88230f902"
    contact_id = "ecb48aaa-fa4f-4251-97cf-e021f1a1879c"
    phone = "5581985806544@s.whatsapp.net"

    row = await client.from_("appointments").select("*").eq("id", db_id).single().execute()
    print("Before:", row.data)
    assert row.data["status"] == "canceled"
    assert row.data["patient_id"] == patient_id

    start = datetime(2026, 7, 15, 16, 0, tzinfo=TZ)

    new_event_id = await create_event(
        calendar_id, start, 60, "Eduardo Henrique Cordeiro Dos Santos",
        "Bruna", modality="presencial", patient_email="aizakellykelly@gmail.com",
        patient_number=phone,
    )
    print("New calendar event:", new_event_id)

    now_iso = datetime.now(TZ).isoformat()
    r = await client.from_("appointments").update({
        "status": "scheduled",
        "appointment_id": new_event_id,
        "booking_fee_waived": True,
        "booking_fee_paid_at": now_iso,
        "payment_reminder_sent_at": None,
        "updated_at": now_iso,
    }).eq("id", db_id).execute()
    print("Updated:", r.data)

    await log_event("appointment_restored_fee_waiver_bug", "5581985806544", {
        "db_id": db_id,
        "old_event_id": row.data["appointment_id"],
        "new_event_id": new_event_id,
        "reason": "Consulta cancelada por falta de pagamento apesar de taxa isentada por instrução da atendente; nunca foi persistido no banco. Restaurada manualmente.",
    })
    print("Done.")

asyncio.run(main())
