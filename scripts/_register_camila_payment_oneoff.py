"""
One-off: registra o pagamento de Camila Marques Brasileiro (27/07/2026 14:00,
Dr. Júlio, presencial) na planilha Pagamentos.

O pagamento foi confirmado no dashboard mas não foi registrado na planilha por
um bug de validação (updatedRange vazio não era detectado). Este script
registra manualmente usando a função corrigida (app.google_sheets.append_payment_receipt).

Uso: uv run python scripts/_register_camila_payment_oneoff.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

TZ_PHONE = "5581987516312@s.whatsapp.net"
PATIENT_NAME = "Camila Marques Brasileiro"
DOCTOR_LABEL = "Dr. Júlio"
APPOINTMENT_DT = "27/07/2026 14:00"
AMOUNT = "650"
PAYMENT_TYPE = "Consulta"
PAYMENT_METHOD = "Dinheiro"


async def main():
    from app.google_sheets import append_payment_receipt

    print(f"📝 Registrando pagamento na planilha:")
    print(f"   Paciente: {PATIENT_NAME}")
    print(f"   Médico: {DOCTOR_LABEL}")
    print(f"   Consulta: {APPOINTMENT_DT}")
    print(f"   Valor: R$ {AMOUNT}")
    print(f"   Tipo: {PAYMENT_TYPE}")
    print(f"   Forma: {PAYMENT_METHOD}")

    try:
        await append_payment_receipt(
            patient_name=PATIENT_NAME,
            phone=TZ_PHONE,
            doctor_name=DOCTOR_LABEL,
            appointment_dt=APPOINTMENT_DT,
            amount=AMOUNT,
            payment_type=PAYMENT_TYPE,
            payment_method_override=PAYMENT_METHOD,
            drive_link="",
        )
        print("✅ Pagamento registrado com sucesso na planilha Pagamentos!")
    except Exception as e:
        print(f"❌ Erro ao registrar: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
