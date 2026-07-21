"""Registrar retroativamente o link do comprovante de pagamento (taxa de reserva) de Isaac Caleb Silva Amaral.

A taxa de reserva foi paga e registrada na planilha de Pagamentos, mas o link do comprovante
não foi registrado no banco de dados. Este script completa o registro.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")

APPOINTMENT_ID = "f07c410f-9652-4563-ad9d-e2ae7a84a98c"
PATIENT_NAME = "Isaac Caleb Silva Amaral"
PATIENT_PHONE = "5581987385089"
DOCTOR_LABEL = "Júlio"
APPOINTMENT_DT = "03/08/2026 11:00"
AMOUNT = "150,00"
DRIVE_LINK = "https://drive.google.com/file/d/1YzpYTPjiPYqTVj8B4C5VP4xLC_rl4cSH/view?usp=drive_link"


async def main():
    from app.database import get_supabase, log_event

    client = await get_supabase()

    # Verify appointment exists and is already marked as paid
    appt = (await client.from_("appointments").select("*").eq("id", APPOINTMENT_ID).execute()).data
    if not appt:
        print("❌ Agendamento não encontrado — abortando.")
        return

    appt = appt[0]
    if not appt.get("booking_fee_paid_at"):
        print("❌ Taxa de reserva ainda não registrada — abortando.")
        return

    print("✅ Agendamento verificado:")
    print(f"   Paciente: {PATIENT_NAME}")
    print(f"   Taxa paga em: {appt.get('booking_fee_paid_at')}")
    print()

    # Register the payment receipt event with drive_link
    print("📝 Registrando link do comprovante no banco de dados...")
    try:
        await log_event("payment_receipt_registered", PATIENT_PHONE, {
            "patient_name": PATIENT_NAME,
            "amount": AMOUNT,
            "payment_type": "Taxa de Reserva",
            "payment_method": "PIX",
            "drive_link": DRIVE_LINK,
            "appointment_id": APPOINTMENT_ID,
            "registered_via": "dashboard",
            "retroactive_registration": True,
        })
        print("✅ Evento registrado com sucesso em `events`!")
        print()
        print(f"📋 Detalhes registrados:")
        print(f"   Paciente: {PATIENT_NAME}")
        print(f"   Médico: {DOCTOR_LABEL}")
        print(f"   Consulta: {APPOINTMENT_DT}")
        print(f"   Taxa: R$ {AMOUNT}")
        print(f"   Link: {DRIVE_LINK}")
        print()
        print("✅ Fluxo completo — link do comprovante registrado no BD!")

    except Exception as e:
        print(f"❌ Erro ao registrar evento: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
