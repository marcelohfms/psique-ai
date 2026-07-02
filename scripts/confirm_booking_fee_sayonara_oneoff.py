"""
One-off: registra pagamento da taxa de reserva da Sayonara Lira (R$100 PIX, 11/06/2026 12:03).
Consulta: 17/06/2026 11:00, Dra. Bruna.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "558195302944@s.whatsapp.net"
APPOINTMENT_ID = "u8jeb80dh875oj3lv266ffoi64"
DRIVE_LINK = "https://drive.google.com/file/d/1bVzZT3Talrn901HO-PXhfL0ZYULeTjAZ/view?usp=drivesdk"
PAYMENT_DT = datetime(2026, 6, 11, 12, 3, 35, tzinfo=TZ)
AMOUNT = "100,00"
PATIENT_NAME = "Sayonara Lira"
DOCTOR_LABEL = "Dra. Bruna"


async def main():
    from app.database import get_supabase, log_event
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()

    # 1. Registrar na planilha
    appointment_dt = "17/06/2026 11:00"
    try:
        await append_payment_receipt(
            patient_name=PATIENT_NAME,
            phone=PHONE,
            doctor_name=DOCTOR_LABEL,
            appointment_dt=appointment_dt,
            amount=AMOUNT,
            drive_link=DRIVE_LINK,
            payment_type="Taxa de reserva",
            payment_method_override="PIX",
        )
        print("✅ Registrado na planilha.")
    except Exception as e:
        print(f"⚠️  Planilha: {e}")

    # 2. Marcar booking_fee_paid_at no banco
    await client.from_("appointments").update({
        "booking_fee_paid_at": PAYMENT_DT.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print(f"✅ booking_fee_paid_at = {PAYMENT_DT.strftime('%d/%m/%Y %H:%M:%S')} salvo.")

    # 3. Confirmar para a Sayonara via WhatsApp
    msg = (
        "Sayonara, o pagamento da taxa de reserva foi confirmado! 🎉\n\n"
        "Sua consulta está garantida:\n"
        "📅 *Quarta-feira, 17/06/2026 às 11:00*\n"
        "👩‍⚕️ Dra. Bruna\n"
        "📍 Presencial\n\n"
        "Até quarta-feira! 😊🩷"
    )
    try:
        conv_id = await find_or_create_conversation(PHONE)
        await send_message(conv_id, msg)
        print(f"✅ Confirmação enviada (conv_id={conv_id})")
    except Exception as e:
        print(f"⚠️  WhatsApp: {e}")

    # 4. Notificar clínica
    phone_clean = PHONE.replace("@s.whatsapp.net", "")
    try:
        await send_clinic_notification_email(
            f"Taxa de reserva registrada — {PATIENT_NAME}",
            f"Paciente: {PATIENT_NAME}\nTelefone: {phone_clean}\nMédico: {DOCTOR_LABEL}\n"
            f"Consulta: {appointment_dt}\nValor: R$ 100,00 (taxa de reserva)\nForma: PIX\n"
            f"Data do pagamento: {PAYMENT_DT.strftime('%d/%m/%Y %H:%M')}\nComprovante: {DRIVE_LINK}\n\n"
            "Registro feito manualmente (bug corrigido).",
        )
        print("✅ Clínica notificada por e-mail.")
    except Exception as e:
        print(f"⚠️  E-mail: {e}")

    await log_event("booking_fee_registered", PHONE, {
        "patient_name": PATIENT_NAME, "amount": AMOUNT, "drive_link": DRIVE_LINK,
        "note": "registro manual — bug pending_appointment + ImportError send_text",
    })
    print("✅ Evento registrado.")


asyncio.run(main())
