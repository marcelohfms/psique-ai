import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581981139373@s.whatsapp.net"
APPOINTMENT_ID = "rj02m9ar2ffi1t181tluuk293k"
DRIVE_LINK = "https://drive.google.com/file/d/1SqtX2GRF_CVHzvXJK5mCcQ8NVkuDAPOq/view?usp=drive_link"
AMOUNT = "100,00"
PATIENT_NAME = "Maria José Alves de Farias"
CONTACT_NAME = "Valdenia"
DOCTOR_LABEL = "Dra. Bruna"
APPOINTMENT_DT = "17/06/2026 09:00"

async def main():
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email

    # Planilha
    try:
        await append_payment_receipt(
            patient_name=PATIENT_NAME,
            phone=PHONE,
            doctor_name=DOCTOR_LABEL,
            appointment_dt=APPOINTMENT_DT,
            amount=AMOUNT,
            drive_link=DRIVE_LINK,
            payment_type="Taxa de reserva",
            payment_method_override="PIX",
        )
        print("✅ Registrado na planilha.")
    except Exception as e:
        print(f"⚠️  Planilha: {e}")

    # E-mail clínica
    phone_clean = PHONE.replace("@s.whatsapp.net", "")
    try:
        await send_clinic_notification_email(
            f"Taxa de reserva registrada — {PATIENT_NAME}",
            f"Paciente: {PATIENT_NAME}\n"
            f"Responsável: {CONTACT_NAME}\n"
            f"Telefone: {phone_clean}\n"
            f"Médico(a): {DOCTOR_LABEL}\n"
            f"Consulta: {APPOINTMENT_DT}\n"
            f"Valor: R$ 100,00 (taxa de reserva)\n"
            f"Forma: PIX\n"
            f"Comprovante: {DRIVE_LINK}\n\n"
            "Registro feito manualmente via script."
        )
        print("✅ E-mail enviado para a clínica.")
    except Exception as e:
        print(f"⚠️  E-mail: {e}")

asyncio.run(main())
