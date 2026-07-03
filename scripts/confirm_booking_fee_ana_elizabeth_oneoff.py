"""
One-off: envia disparo de consulta agendada e registra pagamento da taxa de reserva
(R$ 100,00, dinheiro, presencial) para Ana Elizabeth Diniz de Barros — 16/06/2026 às 16:00.
Uso: uv run python scripts/confirm_booking_fee_ana_elizabeth_oneoff.py
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581998782468@s.whatsapp.net"
PAYMENT_DT = datetime(2026, 6, 9, tzinfo=TZ)
AMOUNT = "100,00"
DOCTOR_LABEL = "Dr. Júlio"


async def main():
    from app.database import get_supabase, get_user_by_phone, log_event
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email
    from app.chatwoot import find_or_create_conversation, send_message

    user = await get_user_by_phone(PHONE)
    if not user:
        print(f"❌ Usuário não encontrado para {PHONE}")
        return

    patient_name = user.get("patient_name") or user.get("name", "Paciente")
    first_name = patient_name.split()[0]
    user_id = user["id"]
    print(f"Paciente: {patient_name} (id={user_id})")

    client = await get_supabase()

    # Busca consulta agendada
    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, booking_fee_paid_at, status"
    ).eq("user_id", user_id).eq("status", "scheduled").order("start_time").limit(1).execute()

    if not appt_result.data:
        print("❌ Nenhuma consulta agendada encontrada.")
        return

    appt = appt_result.data[0]
    apt_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
    appointment_id = appt["appointment_id"]
    print(f"Consulta: {appointment_dt} (ID: {appointment_id})")

    # --- 1. Disparo de consulta agendada via WhatsApp ---
    pix_key = __import__("os").environ.get("PIX_KEY", "42006848000178")
    disparo_msg = (
        f"Olá, {first_name}! 😊\n\n"
        f"Sua consulta com o *{DOCTOR_LABEL}* está confirmada para o dia "
        f"*{apt_start.strftime('%d/%m/%Y')} às {apt_start.strftime('%H:%M')}*.\n\n"
        f"Para garantir sua vaga, realize o pagamento da taxa de reserva de *R$ 100,00* "
        f"via PIX ({pix_key}) e envie o comprovante aqui no chat.\n\n"
        f"Qualquer dúvida, estamos à disposição! 🙏"
    )
    try:
        conv_id = await find_or_create_conversation(PHONE)
        await send_message(conv_id, disparo_msg)
        print(f"✅ Disparo enviado (conv_id={conv_id})")
    except Exception as e:
        print(f"⚠️  Falha ao enviar disparo: {e}")

    # --- 2. Registrar na planilha de pagamentos ---
    try:
        await append_payment_receipt(
            patient_name=patient_name,
            phone=PHONE,
            doctor_name=DOCTOR_LABEL,
            appointment_dt=appointment_dt,
            amount=AMOUNT,
            drive_link="",
            payment_type="Taxa de reserva",
            payment_method_override="Dinheiro",
        )
        print("✅ Registrado na planilha de pagamentos.")
    except Exception as e:
        print(f"⚠️  Falha ao registrar na planilha: {e}")

    # --- 3. Marcar taxa de reserva como paga no Supabase ---
    if appt.get("booking_fee_paid_at"):
        print(f"⚠️  Taxa já marcada como paga em {appt['booking_fee_paid_at']}. Atualizando mesmo assim.")
    await client.from_("appointments").update({
        "booking_fee_paid_at": PAYMENT_DT.isoformat(),
    }).eq("appointment_id", appointment_id).execute()
    print(f"✅ booking_fee_paid_at = {PAYMENT_DT.strftime('%d/%m/%Y')} registrado no banco.")

    # --- 4. Notificar clínica por e-mail ---
    phone_clean = PHONE.replace("@s.whatsapp.net", "")
    notify_msg = (
        f"💰 Taxa de reserva registrada manualmente — {patient_name}\n\n"
        f"Paciente: {patient_name}\n"
        f"Telefone: {phone_clean}\n"
        f"Médico: {DOCTOR_LABEL}\n"
        f"Consulta: {appointment_dt}\n"
        f"Valor: R$ 100,00 (taxa de reserva)\n"
        f"Forma: Dinheiro (presencial)\n"
        f"Data do pagamento: {PAYMENT_DT.strftime('%d/%m/%Y')}\n\n"
        "Registro feito manualmente via script."
    )
    try:
        await send_clinic_notification_email(
            f"Taxa de reserva registrada — {patient_name}",
            notify_msg,
        )
        print("✅ Notificação enviada ao e-mail da clínica.")
    except Exception as e:
        print(f"⚠️  Falha ao notificar clínica: {e}")

    await log_event("booking_fee_registered", PHONE, {
        "patient_name": patient_name,
        "amount": AMOUNT,
        "payment_method": "Dinheiro",
        "note": "registro manual one-off — dinheiro presencial 09/06/2026",
    })
    print("✅ Evento registrado.")


if __name__ == "__main__":
    asyncio.run(main())
