import asyncio, os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5587981054742"
APPOINTMENT_ID = "abcmv3s0lbtgf8oe8cvp0bu1mg"
DOCTOR_LABEL = "Dr. Júlio"
DATE_STR = "22/06/2026 às 10:00"
PIX_KEY = os.environ.get("PIX_KEY", "42006848000178")

async def main():
    from app.database import get_supabase
    from scripts.send_payment_reminders import payment_cancel_message, send_whatsapp

    # Dados do contato
    client = await get_supabase()
    user_r = await client.from_("users").select("name, patient_name") \
        .eq("number", PHONE).single().execute()
    u = user_r.data
    contact_name = u.get("name") or u.get("patient_name") or "paciente"
    patient_name = u.get("patient_name") or ""
    contact_first = contact_name.split()[0]
    patient_first = patient_name.split()[0] if patient_name and patient_name != contact_name else None

    msg = payment_cancel_message(contact_first, DOCTOR_LABEL, DATE_STR, patient_first)
    print(f"Mensagem:\n{msg}\n")

    # Envia
    await send_whatsapp(PHONE, msg)
    print("✅ Aviso de cancelamento enviado.")

    # Atualiza updated_at para agora
    now = datetime.now(TZ)
    await client.from_("appointments").update({
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print(f"✅ updated_at = {now.strftime('%d/%m/%Y %H:%M')} registrado.")

asyncio.run(main())
