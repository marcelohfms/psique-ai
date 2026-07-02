import asyncio, re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "558181963813@s.whatsapp.net"
USER_ID = "aaaf8986-06fa-4271-9be1-644f198dfe47"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"
PATIENT_NAME = "Vicente Ximênes Lopes Novaes Gonçalves"
CONTACT_NAME = "Sandra Karoline Ximenes"
DOCTOR_LABEL = "Dr. Júlio"
NEW_START = datetime(2026, 6, 30, 14, 0, tzinfo=TZ)
SLOT_MINUTES = 60
DRIVE_LINK = ""  # comprovante sem drive_link — pagamento integral direto
AMOUNT = "700,00"

async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()

    # Busca drive_link na conversa
    msgs = await client.from_("messages").select("content") \
        .eq("phone", "558181963813").ilike("content", "%drive_link%") \
        .order("created_at", desc=True).limit(1).execute()
    drive_link = ""
    if msgs.data:
        links = re.findall(r'drive_link:(https?://[^\]]+)', msgs.data[0]["content"])
        if links:
            drive_link = links[0]
    print(f"Drive link: {drive_link or 'não encontrado'}")

    # 1. Cria evento no Calendar
    calendar_id = await _get_doctor_calendar_id("julio")
    event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality="presencial",
        patient_number=PHONE,
    )
    print(f"✅ Evento criado: {event_id}")

    # 2. Salva agendamento com booking_fee + paid_at
    now = datetime.now(TZ)
    end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").insert({
        "user_id": USER_ID,
        "doctor_id": DOCTOR_ID_JULIO,
        "appointment_id": event_id,
        "start_time": NEW_START.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "modality": "presencial",
        "booking_fee_paid_at": now.isoformat(),
        "paid_at": now.isoformat(),
    }).execute()
    print("✅ Agendamento salvo com pagamento integral registrado")

    # 3. Planilha
    appointment_dt = "30/06/2026 14:00"
    try:
        await append_payment_receipt(
            patient_name=PATIENT_NAME,
            phone=PHONE,
            doctor_name=DOCTOR_LABEL,
            appointment_dt=appointment_dt,
            amount=AMOUNT,
            drive_link=drive_link,
            payment_type="Consulta",
            payment_method_override="PIX",
        )
        print("✅ Planilha atualizada")
    except Exception as e:
        print(f"⚠️  Planilha: {e}")

    # 4. Notifica clínica
    await send_clinic_notification_email(
        f"Agendamento + pagamento registrado — {PATIENT_NAME}",
        f"Paciente: {PATIENT_NAME}\nResponsável: {CONTACT_NAME}\n"
        f"Médico(a): {DOCTOR_LABEL}\nConsulta: {appointment_dt}\n"
        f"Valor: R$ 700,00 (consulta completa)\nForma: PIX\n"
        f"Comprovante: {drive_link or 'não disponível'}\n\n"
        "Registro feito manualmente."
    )
    print("✅ Clínica notificada por e-mail")

    # 5. Confirma para Sandra via WhatsApp
    msg = (
        f"Sandra, tudo confirmado! 🎉\n\n"
        f"Consulta do Vicente agendada e pagamento registrado:\n\n"
        f"📅 *30/06/2026 às 14:00*\n"
        f"👨‍⚕️ {DOCTOR_LABEL}\n"
        f"📍 Presencial\n"
        f"💳 Pagamento: R$ 700,00 (PIX) — quitado ✅\n\n"
        f"Qualquer dúvida, estamos à disposição! 🩷"
    )
    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Sandra notificada (conv_id={conv_id})")

asyncio.run(main())
