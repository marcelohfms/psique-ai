"""
One-off: confirma pagamento e finaliza agendamento para o paciente 555491286607.
Uso: uv run python scripts/confirm_payment_oneoff.py
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

PHONE = "555491286607@s.whatsapp.net"
TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase, get_user_by_phone, log_event, DOCTOR_NAMES
    from app.whatsapp import send_text
    from app.google_sheets import append_payment_receipt

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

    # Busca próxima consulta agendada
    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, paid_at"
    ).eq("user_id", user_id).eq("status", "scheduled").order("start_time").limit(1).execute()

    if not appt_result.data:
        print("❌ Nenhuma consulta agendada encontrada para este paciente.")
        return

    appt = appt_result.data[0]
    apt_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
    appointment_id = appt["appointment_id"]

    print(f"Consulta:  {appointment_dt} (ID: {appointment_id})")

    if appt.get("paid_at"):
        print(f"⚠️  Consulta já marcada como paga em {appt['paid_at']}. Prosseguindo mesmo assim.")

    # Registra na planilha
    try:
        await append_payment_receipt(
            patient_name=patient_name,
            phone=PHONE,
            doctor_name=doctor_label,
            appointment_dt=appointment_dt,
            amount="100,00",
            drive_link="(comprovante manual — chave PIX não lida corretamente pela Eva)",
        )
        print("✅ Registrado na planilha de pagamentos.")
    except Exception as e:
        print(f"⚠️  Falha ao registrar na planilha: {e}")

    # Marca como pago no Supabase
    await client.from_("appointments").update({
        "paid_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()
    print("✅ Consulta marcada como paga no banco.")

    # Envia confirmação ao paciente
    msg = (
        f"Olá, {patient_name.split()[0]}! 😊 Recebemos o seu comprovante de pagamento "
        f"e sua vaga com {doctor_label} no dia {appointment_dt} está confirmada! ✅\n\n"
        "Qualquer dúvida, estamos à disposição."
    )
    await send_text(PHONE, msg)
    print(f"✅ Mensagem de confirmação enviada ao paciente.")

    await log_event("payment_receipt_registered", PHONE, {
        "patient_name": patient_name,
        "amount": "100,00",
        "note": "confirmação manual — chave PIX não reconhecida pela Eva",
    })
    print("✅ Evento registrado.")


if __name__ == "__main__":
    asyncio.run(main())
