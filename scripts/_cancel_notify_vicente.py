import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from scripts.send_payment_reminders import payment_cancel_message, send_whatsapp
    from app.email_sender import send_clinic_notification_email

    users = await get_users_by_phone("5581981963813@s.whatsapp.net")
    u = users[0]
    phone = u["number"]
    contact_name = u.get("name") or u.get("patient_name") or "paciente"
    patient_name = u.get("patient_name") or ""
    contact_first = contact_name.split()[0]
    patient_first = patient_name.split()[0] if patient_name and patient_name != contact_name else None

    msg = payment_cancel_message(contact_first, "Dr. Júlio", "23/06/2026 às 14:00", patient_first)
    print(f"Mensagem:\n{msg}\n")

    await send_whatsapp(phone, msg)
    print("✅ Aviso enviado para Vicente.")

    await send_clinic_notification_email(
        f"Consulta cancelada por falta de pagamento — {patient_name or contact_name}",
        f"Paciente: {patient_name or contact_name}\nResponsável: {contact_name}\n"
        f"Médico(a): Dr. Júlio\nData/hora: 23/06/2026 às 14:00\nWhatsApp: {phone}\n\n"
        f"Aviso enviado manualmente em 11/06 (cancelamento ocorreu em 11/06 00:28 sem notificação)."
    )
    print("✅ Clínica notificada por e-mail.")

asyncio.run(main())
