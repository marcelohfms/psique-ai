"""
One-off: envia confirmação de pagamento para Camila Marques Brasileiro.

Camila pagou a consulta (27/07/2026 14:00 com Dr. Júlio) e agora recebe
confirmação via WhatsApp.

Uso: uv run python scripts/_confirm_camila_payment_oneoff.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581987516312"
PATIENT_NAME = "Camila"
DOCTOR_LABEL = "Dr. Júlio"
APPOINTMENT_DT = "27/07"
AMOUNT = "650"


async def main():
    from app.whatsapp import send_text

    message = (
        f"Olá, {PATIENT_NAME}! 👋\n\n"
        f"Recebemos o seu pagamento de R$ {AMOUNT} referente à consulta "
        f"com {DOCTOR_LABEL} de hoje, {APPOINTMENT_DT} às 14h. ✅\n\n"
        f"Obrigado! Qualquer dúvida, estou à disposição!"
    )

    print("📱 Enviando confirmação de pagamento para Camila...")
    print(f"Mensagem:\n{message}\n")

    await send_text(PHONE, message)
    print("✅ Mensagem enviada com sucesso!")


if __name__ == "__main__":
    asyncio.run(main())
