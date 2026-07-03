import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
OLD_APPT_ID = "anv0867ljg2f8ufda234u6cv8k"
NEW_START = datetime(2026, 6, 17, 16, 0, tzinfo=TZ)
SLOT_MINUTES = 60
PATIENT_NAME = "Heitor José Cirilo Danda"
CONTACT_NAME = "Ana Paula de Lima Cirilo"
PHONE = "558196001122@s.whatsapp.net"
DOCTOR_LABEL = "Dra. Bruna"
DRIVE_LINK = "https://drive.google.com/file/d/1__w4is3PFJERCOMOWxLy_x7Fhsc82odW/view?usp=drivesdk"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()
    calendar_id = await _get_doctor_calendar_id("bruna")

    # Recria evento
    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality="presencial",
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {new_event_id}")

    # Reativa appointment
    now = datetime.now(TZ)
    end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "start_time": NEW_START.isoformat(),
        "end_time": end.isoformat(),
        "booking_fee_paid_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }).eq("appointment_id", OLD_APPT_ID).execute()
    print("✅ Consulta reativada: 17/06/2026 16:00 | status=scheduled")

    # Planilha
    try:
        await append_payment_receipt(
            patient_name=PATIENT_NAME,
            phone=PHONE,
            doctor_name=DOCTOR_LABEL,
            appointment_dt="17/06/2026 16:00",
            amount="100,00",
            drive_link=DRIVE_LINK,
            payment_type="Taxa de reserva",
            payment_method_override="PIX",
        )
        print("✅ Registrado na planilha.")
    except Exception as e:
        print(f"⚠️  Planilha: {e}")

    # E-mail clínica
    await send_clinic_notification_email(
        f"Taxa de reserva registrada — {PATIENT_NAME}",
        f"Paciente: {PATIENT_NAME}\nResponsável: {CONTACT_NAME}\nTelefone: 558196001122\n"
        f"Médico(a): {DOCTOR_LABEL}\nConsulta: 17/06/2026 16:00\nValor: R$ 100,00\n"
        f"Comprovante: {DRIVE_LINK}\n\nConsulta reativada manualmente após pagamento pós-cancelamento."
    )
    print("✅ Clínica notificada.")

    # Confirma para Ana Paula
    msg = (
        "Ana Paula, tudo certo! 😊 O pagamento da taxa de reserva foi confirmado e "
        "a consulta do Heitor está garantida:\n\n"
        "📅 *17/06/2026 às 16:00*\n"
        "👩‍⚕️ Dra. Bruna\n"
        "📍 Presencial\n\n"
        "Qualquer dúvida, estamos à disposição! 🩷"
    )
    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Mensagem enviada (conv_id={conv_id})")

asyncio.run(main())
