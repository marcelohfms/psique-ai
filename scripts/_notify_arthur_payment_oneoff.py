"""
One-off: envia à clínica a notificação de pagamento de Arthur Tenório Ribeiro Clark
que o dashboard deixou de enviar (falha silenciosa de e-mail em 27/07/2026).

Reproduz exatamente o e-mail que `mark_paid` (dashboard/payments.py) teria enviado
no ramo com comprovante.

Uso: uv run python scripts/_notify_arthur_payment_oneoff.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_NAME = "Arthur Tenório Ribeiro Clark"
PHONE = "5581996503841"
DOCTOR_LABEL = "Dra. Bruna"
APPOINTMENT_DT = "27/07/2026 16:00"
AMOUNT = "550"
PAYMENT_TYPE = "Consulta"
DRIVE_LINK = "https://drive.google.com/file/d/1vwPdWZm4K3jia-9ngJlDNCbEvFkgJmF5/view?usp=drivesdk"


async def main():
    from app.graph.tools import _notify_clinic

    message = (
        f"💰 Comprovante recebido!\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Valor: R$ {AMOUNT}\n"
        f"Tipo: {PAYMENT_TYPE}\n"
        f"Consulta: {APPOINTMENT_DT}\n"
        f"Link: {DRIVE_LINK}\n\n"
        f"(Reenviado por script — o e-mail original do dashboard falhou silenciosamente "
        f"em 27/07/2026. Pagamento já consta na planilha Pagamentos e no agendamento.)"
    )

    print("📧 Enviando notificação à clínica...")
    await _notify_clinic(
        message,
        phone=PHONE,
        subject=f"Comprovante recebido — {PATIENT_NAME}",
    )
    print("✅ Notificação enviada.")


if __name__ == "__main__":
    asyncio.run(main())
