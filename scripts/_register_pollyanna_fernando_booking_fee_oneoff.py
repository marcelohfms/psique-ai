"""
One-off: registra manualmente a taxa de reserva (R$ 100,00) paga pela mãe do paciente
Fernando Daniel Bezerra de França Santino (contato 5581994358739), cujo comprovante
nunca foi processado pela Eva — o anexo chegou sem legenda e caiu num gap conhecido
do reprocessamento por label eva-ativa (corrigido em app/chatwoot.py/app/main.py).

Lido/confirmado por scripts/_force_read_comprovante_5581994358739.py:
  Valor: R$ 100,00 (PIX, CNPJ Psique 42006848000178, 31/07 13:06)
  Drive: https://drive.google.com/file/d/1kCQjUmbfWgeMXBEH6jPlLHU7dN1InOXk/view?usp=drivesdk
  (já renomeado para Fernando_Daniel_Bezerra_de_França_Santino_06-08-2026_R$100-00.jpg)

Uso: uv run python scripts/_register_pollyanna_fernando_booking_fee_oneoff.py
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PHONE = "5581994358739@s.whatsapp.net"
PATIENT_ID = "0ce48863-71f6-4725-9cea-446a71826d2f"
APPOINTMENT_ID = "c1orgha6obtbnmigni9imsqp38"
DRIVE_LINK = "https://drive.google.com/file/d/1kCQjUmbfWgeMXBEH6jPlLHU7dN1InOXk/view?usp=drivesdk"
AMOUNT = "100,00"
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_NAME = "Fernando Daniel Bezerra de França Santino"
TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase, log_event
    from app.google_sheets import append_payment_receipt
    from app.graph.tools import _notify_clinic
    from app.whatsapp import send_text

    client = await get_supabase()

    appt = await client.from_("appointments").select(
        "appointment_id, start_time, booking_fee_paid_at, status"
    ).eq("appointment_id", APPOINTMENT_ID).maybe_single().execute()
    if not appt.data:
        print("❌ Consulta não encontrada.")
        return
    if appt.data.get("booking_fee_paid_at"):
        print(f"⚠️  Já marcada como paga em {appt.data['booking_fee_paid_at']}. Abortando para evitar duplicidade.")
        return
    if appt.data.get("status") != "scheduled":
        print(f"⚠️  Consulta com status inesperado: {appt.data.get('status')}. Abortando.")
        return

    apt_start = datetime.fromisoformat(appt.data["start_time"]).astimezone(TZ)
    appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
    now_iso = datetime.now(TZ).isoformat()

    # 1. Marca a taxa de reserva como paga
    await client.from_("appointments").update({
        "booking_fee_paid_at": now_iso,
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print(f"✅ booking_fee_paid_at registrado ({appointment_dt}).")

    # 2. Planilha de pagamentos
    try:
        await append_payment_receipt(
            PATIENT_NAME, PHONE, DOCTOR_LABEL, appointment_dt, AMOUNT, DRIVE_LINK,
            payment_type="Taxa de Reserva",
        )
        print("✅ Registrado na planilha de pagamentos.")
    except Exception as e:
        print(f"⚠️  Falha ao registrar na planilha: {e}")

    # 3. Notifica a clínica
    try:
        await _notify_clinic(
            f"💰 Comprovante recebido! (registro manual — comprovante não capturado automaticamente pela Eva)\n"
            f"Paciente: {PATIENT_NAME}\nValor: R$ {AMOUNT}\nTipo: Taxa de Reserva\n"
            f"Consulta: {appointment_dt}\nLink: {DRIVE_LINK}",
            subject=f"Comprovante recebido — {PATIENT_NAME}",
        )
        print("✅ Clínica notificada por e-mail.")
    except Exception as e:
        print(f"⚠️  Falha ao notificar a clínica: {e}")

    # 4. Confirmação para a paciente
    msg = (
        f"Olá, Pollyanna! 😊 Recebemos o seu comprovante de pagamento "
        f"e a vaga do Fernando Daniel com {DOCTOR_LABEL} no dia {appointment_dt} está confirmada! ✅\n\n"
        "Saldo restante para quitação no dia da consulta: R$ 600,00 (com desconto PIX)."
    )
    await send_text(PHONE, msg)
    print("✅ Mensagem de confirmação enviada à paciente.")

    await log_event("payment_receipt_registered", PHONE, {
        "patient_name": PATIENT_NAME,
        "amount": AMOUNT,
        "payment_type": "Taxa de Reserva",
        "drive_link": DRIVE_LINK,
        "note": "confirmação manual — comprovante sem legenda não capturado pela Eva (bug corrigido)",
    })
    print("✅ Evento registrado.")


if __name__ == "__main__":
    asyncio.run(main())
