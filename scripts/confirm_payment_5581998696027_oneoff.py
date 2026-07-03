"""
One-off: confirma pagamento da consulta de 26/05 para 5581998696027.
Comprovante: https://drive.google.com/file/d/17hgvdmZjMlA7jmmoxA55Nub5IMa3fIBD/view?usp=drive_link
Uso: uv run python scripts/confirm_payment_5581998696027_oneoff.py
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PHONE = "5581998696027@s.whatsapp.net"
DRIVE_LINK = "https://drive.google.com/file/d/17hgvdmZjMlA7jmmoxA55Nub5IMa3fIBD/view?usp=drive_link"
PAYMENT_DT = datetime(2026, 5, 26, 13, 28, tzinfo=ZoneInfo("America/Recife"))
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

    # Busca consulta do dia 26/05 (agendada ou já concluída)
    day_start = datetime(2026, 5, 26, 0, 0, tzinfo=TZ).isoformat()
    day_end   = datetime(2026, 5, 26, 23, 59, tzinfo=TZ).isoformat()
    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, paid_at, status"
    ).eq("user_id", user_id).gte("start_time", day_start).lte("start_time", day_end).order("start_time").limit(1).execute()

    if not appt_result.data:
        print("⚠️  Nenhuma consulta encontrada em 26/05 — buscando a última consulta agendada...")
        appt_result = await client.from_("appointments").select(
            "appointment_id, start_time, paid_at, status"
        ).eq("user_id", user_id).order("start_time", desc=True).limit(1).execute()

    if not appt_result.data:
        print("❌ Nenhuma consulta encontrada para este paciente.")
        return

    appt = appt_result.data[0]
    apt_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
    appointment_id = appt["appointment_id"]

    print(f"Consulta:  {appointment_dt} (ID: {appointment_id}, status: {appt['status']})")

    if appt.get("paid_at"):
        print(f"⚠️  Consulta já marcada como paga em {appt['paid_at']}. Prosseguindo mesmo assim.")

    # Registra na planilha de pagamentos
    try:
        await append_payment_receipt(
            patient_name=patient_name,
            phone=PHONE,
            doctor_name=doctor_label,
            appointment_dt=appointment_dt,
            amount="100,00",
            drive_link=DRIVE_LINK,
            payment_type="PIX",
        )
        print("✅ Registrado na planilha de pagamentos.")
    except Exception as e:
        print(f"⚠️  Falha ao registrar na planilha: {e}")

    # Marca como pago no Supabase com o horário do comprovante
    await client.from_("appointments").update({
        "paid_at": PAYMENT_DT.isoformat(),
    }).eq("appointment_id", appointment_id).execute()
    print("✅ Consulta marcada como paga no banco (paid_at = 26/05 13:28 BRT).")

    # Notifica e-mail da clínica
    phone_clean = PHONE.replace("@s.whatsapp.net", "")
    notify_msg = (
        f"💰 Pagamento registrado manualmente — {patient_name}\n\n"
        f"Paciente: {patient_name}\n"
        f"Telefone: {phone_clean}\n"
        f"Médico: {doctor_label}\n"
        f"Consulta: {appointment_dt}\n"
        f"Valor: R$ 100,00\n"
        f"Forma: PIX\n"
        f"Data do pagamento: 26/05/2026 13:28\n"
        f"Comprovante: {DRIVE_LINK}\n\n"
        "Registro feito manualmente via script."
    )
    try:
        await send_clinic_notification_email(
            f"Pagamento registrado — {patient_name}",
            notify_msg,
        )
        print("✅ Notificação enviada ao e-mail da clínica.")
    except Exception as e:
        print(f"⚠️  Falha ao notificar clínica: {e}")

    await log_event("payment_receipt_registered", PHONE, {
        "patient_name": patient_name,
        "amount": "100,00",
        "drive_link": DRIVE_LINK,
        "note": "registro manual one-off — comprovante PIX 26/05 13:28",
    })
    print("✅ Evento registrado.")


if __name__ == "__main__":
    asyncio.run(main())
