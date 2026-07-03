"""
One-off: registra pagamento da TAXA DE RESERVA para 558195168383.
Comprovante: https://drive.google.com/file/d/1VPCNw1EGHCsHaI3V5LdmwZ5Y_hW2Obmt/view?usp=drive_link
Uso: uv run python scripts/confirm_booking_fee_558195168383_oneoff.py
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PHONE = "558195168383@s.whatsapp.net"
DRIVE_LINK = "https://drive.google.com/file/d/1VPCNw1EGHCsHaI3V5LdmwZ5Y_hW2Obmt/view?usp=drive_link"
PAYMENT_DT = datetime(2026, 6, 1, 17, 5, tzinfo=ZoneInfo("America/Recife"))
AMOUNT = "100,00"
TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase, get_user_by_phone, log_event, DOCTOR_NAMES
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email

    user = await get_user_by_phone(PHONE)
    if not user:
        print(f"❌ Usuário não encontrado para {PHONE}")
        return

    patient_name = user.get("patient_name") or user.get("name", "Paciente")
    user_id = user["id"]
    doctor_key = DOCTOR_NAMES.get(user.get("doctor_id", ""), "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")

    print(f"Paciente: {patient_name}")
    print(f"Médico:   {doctor_label}")

    client = await get_supabase()

    # Busca a próxima consulta agendada (taxa de reserva é de consulta futura)
    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, booking_fee_paid_at, status"
    ).eq("user_id", user_id).eq("status", "scheduled").order("start_time").limit(1).execute()

    if not appt_result.data:
        print("⚠️  Nenhuma consulta agendada — buscando a mais recente...")
        appt_result = await client.from_("appointments").select(
            "appointment_id, start_time, booking_fee_paid_at, status"
        ).eq("user_id", user_id).order("start_time", desc=True).limit(1).execute()

    if not appt_result.data:
        print("❌ Nenhuma consulta encontrada para este paciente.")
        return

    appt = appt_result.data[0]
    apt_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
    appointment_id = appt["appointment_id"]

    print(f"Consulta:  {appointment_dt} (ID: {appointment_id}, status: {appt['status']})")

    if appt.get("booking_fee_paid_at"):
        print(f"⚠️  Taxa de reserva já marcada como paga em {appt['booking_fee_paid_at']}. Prosseguindo mesmo assim.")

    # Registra na planilha de pagamentos
    try:
        await append_payment_receipt(
            patient_name=patient_name,
            phone=PHONE,
            doctor_name=doctor_label,
            appointment_dt=appointment_dt,
            amount=AMOUNT,
            drive_link=DRIVE_LINK,
            payment_type="Taxa de reserva",
            payment_method_override="PIX",
        )
        print("✅ Registrado na planilha de pagamentos.")
    except Exception as e:
        print(f"⚠️  Falha ao registrar na planilha: {e}")

    # Marca taxa de reserva como paga no Supabase
    await client.from_("appointments").update({
        "booking_fee_paid_at": PAYMENT_DT.isoformat(),
    }).eq("appointment_id", appointment_id).execute()
    print(f"✅ booking_fee_paid_at = {PAYMENT_DT.strftime('%d/%m/%Y %H:%M')} registrado no banco.")

    # Notifica e-mail da clínica
    phone_clean = PHONE.replace("@s.whatsapp.net", "")
    notify_msg = (
        f"💰 Taxa de reserva registrada manualmente — {patient_name}\n\n"
        f"Paciente: {patient_name}\n"
        f"Telefone: {phone_clean}\n"
        f"Médico: {doctor_label}\n"
        f"Consulta: {appointment_dt}\n"
        f"Valor: R$ 100,00 (taxa de reserva)\n"
        f"Forma: PIX\n"
        f"Data do pagamento: {PAYMENT_DT.strftime('%d/%m/%Y %H:%M')}\n"
        f"Comprovante: {DRIVE_LINK}\n\n"
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
        "drive_link": DRIVE_LINK,
        "note": "registro manual one-off — PIX 01/06/2026 17:05",
    })
    print("✅ Evento registrado.")


if __name__ == "__main__":
    asyncio.run(main())
