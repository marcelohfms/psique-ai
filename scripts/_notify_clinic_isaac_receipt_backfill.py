"""Notifica a clínica por e-mail sobre o comprovante da taxa de reserva de
Isaac Caleb Silva Amaral, no mesmo formato usado por register_payment
(app/graph/tools.py) para o fluxo padrão de comprovante recebido.
"""
import asyncio

PATIENT_NAME = "Isaac Caleb Silva Amaral"
APPOINTMENT_DT = "03/08/2026 11:00"
AMOUNT = "150,00"
PAYMENT_TYPE = "Taxa de Reserva"
DRIVE_LINK = "https://drive.google.com/file/d/1YzpYTPjiPYqTVj8B4C5VP4xLC_rl4cSH/view?usp=drive_link"


async def main():
    from app.email_sender import send_clinic_notification_email

    subject = f"Comprovante recebido — {PATIENT_NAME}"
    body = (
        f"💰 Comprovante recebido!\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Valor: R$ {AMOUNT}\n"
        f"Tipo: {PAYMENT_TYPE}\n"
        f"Consulta: {APPOINTMENT_DT}\n"
        f"Link: {DRIVE_LINK}"
    )
    await send_clinic_notification_email(subject, body)
    print("E-mail de confirmação enviado à clínica.")


if __name__ == "__main__":
    import os
    from pathlib import Path
    for line in Path(".env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v.strip("'\"")
    asyncio.run(main())
