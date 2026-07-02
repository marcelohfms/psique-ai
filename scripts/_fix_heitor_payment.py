import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email

    patient_name = "Heitor Félix Costa Candeia de Lima"
    phone = "5581996937559"
    doctor_label = "Dr. Júlio"
    appointment_dt = "09/07/2026 16:00"
    amount = "100,00"
    drive_link = ""

    await append_payment_receipt(
        patient_name=patient_name,
        phone=phone,
        doctor_name=doctor_label,
        appointment_dt=appointment_dt,
        amount=amount,
        drive_link=drive_link,
        payment_type="Taxa de Reserva",
        payment_method_override="PIX",
    )
    print("✅ Planilha atualizada")

    await send_clinic_notification_email(
        subject=f"Comprovante recebido — {patient_name}",
        body=(
            f"💰 Comprovante recebido!\n"
            f"Paciente: {patient_name}\n"
            f"Valor: R$ 100,00\n"
            f"Tipo: Taxa de Reserva\n"
            f"Consulta: {appointment_dt}\n"
            f"Médico(a): {doctor_label}\n"
            f"Link: (não disponível — registro manual)\n\n"
            "Registro feito manualmente após falha no fluxo automático."
        ),
    )
    print("✅ Email enviado para a clínica")

asyncio.run(main())
