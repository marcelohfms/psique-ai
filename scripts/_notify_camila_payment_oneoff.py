"""
One-off: envia notificação à clínica sobre o pagamento registrado de
Camila Marques Brasileiro.

Uso: uv run python scripts/_notify_camila_payment_oneoff.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_NAME = "Camila Marques Brasileiro"
DOCTOR_LABEL = "Dr. Júlio"
APPOINTMENT_DT = "27/07/2026 14:00"
AMOUNT = "650"
PAYMENT_TYPE = "Consulta"


async def main():
    from app.graph.tools import _notify_clinic

    message = (
        f"💰 Pagamento registrado — {PATIENT_NAME}\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Médico: {DOCTOR_LABEL}\n"
        f"Consulta: {APPOINTMENT_DT}\n"
        f"Tipo: {PAYMENT_TYPE}\n"
        f"Valor: R$ {AMOUNT}\n"
        f"Forma: Dinheiro\n"
        f"Registrado via script de recuperação (bug de falha silenciosa em 27/07/2026)"
    )

    print("📧 Enviando notificação à clínica...")
    await _notify_clinic(
        message,
        phone="5581987516312",
        subject=f"Pagamento registrado — {PATIENT_NAME}",
    )
    print("✅ Notificação enviada com sucesso!")


if __name__ == "__main__":
    asyncio.run(main())
