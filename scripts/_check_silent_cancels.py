import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Consultas canceladas por falta de pagamento (não waived, sem booking_fee_paid)
    appts = await client.from_("appointments").select(
        "appointment_id, start_time, updated_at, payment_reminder_sent_at, doctor_id, users(number, name, patient_name)"
    ).eq("status", "canceled") \
     .eq("booking_fee_waived", False) \
     .is_("booking_fee_paid_at", "null") \
     .not_.is_("payment_reminder_sent_at", "null") \
     .order("updated_at", desc=True) \
     .execute()

    DOCTOR_LABELS = {
        "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
        "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
    }

    print(f"Cancelamentos por falta de pagamento: {len(appts.data)}\n")
    silent = []
    for a in appts.data:
        user = a.get("users") or {}
        phone = (user.get("number") or "").replace("@s.whatsapp.net", "")
        patient = user.get("patient_name") or user.get("name") or "?"
        contact = user.get("name") or "?"
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        canceled_at = datetime.fromisoformat(a["updated_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        doctor = DOCTOR_LABELS.get(a.get("doctor_id", ""), "?")

        # Verifica se há mensagem de cancelamento no histórico
        msgs = await client.from_("messages").select("content") \
            .eq("phone", phone) \
            .ilike("content", "%prazo de 4 horas%") \
            .execute()

        notified = bool(msgs.data)
        if not notified:
            silent.append({
                "phone": phone, "patient": patient, "contact": contact,
                "start": start, "canceled_at": canceled_at, "doctor": doctor,
                "appointment_id": a["appointment_id"],
            })
            print(f"  ❌ SEM AVISO | {patient} ({phone}) | {start} | {doctor} | cancelado em {canceled_at}")
        else:
            print(f"  ✅ avisado   | {patient} ({phone}) | {start} | {doctor}")

    print(f"\nTotal sem aviso: {len(silent)}")

asyncio.run(main())
