"""
One-off: agenda Sayonara Lira para 17/06/2026 às 11:00 (Dra. Bruna, presencial).
Paciente confirmou às 09:19 de 11/06 mas Eva não processou o confirm_appointment.
Uso: uv run python scripts/insert_sayonara_appt_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "558195302944@s.whatsapp.net"          # formato em que a conversa foi salva
PHONE_DB = "5581995302944"                      # número no cadastro users
DOCTOR_ID_BRUNA = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
USER_ID = "6678466d-f898-4ea8-a79e-1743d218f908"
PATIENT_NAME = "Sayonara Lira"
DOCTOR_LABEL = "Dra. Bruna"
NEW_START = datetime(2026, 6, 17, 11, 0, tzinfo=TZ)
SLOT_MINUTES = 60


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()

    # --- 1. Criar evento no Google Calendar ---
    calendar_id = await _get_doctor_calendar_id("bruna")
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

    # --- 2. Inserir agendamento no banco ---
    end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").insert({
        "user_id": USER_ID,
        "doctor_id": DOCTOR_ID_BRUNA,
        "appointment_id": event_id,
        "start_time": NEW_START.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
        "modality": "presencial",
    }).execute()
    print(f"✅ Agendamento salvo: 17/06/2026 às 11:00 — {PATIENT_NAME}")

    # --- 3. Confirmar para a Sayonara via WhatsApp ---
    pix_key = __import__("os").environ.get("PIX_KEY", "42006848000178")
    msg = (
        f"Perfeito, Sayonara! 🎉 Consulta confirmada!\n\n"
        f"📅 Quarta-feira, 17/06/2026 às 11:00\n"
        f"👩‍⚕️ Dra. Bruna\n"
        f"📍 Presencial\n\n"
        f"Para garantir sua vaga, realize o pagamento da taxa de reserva de *R$ 100,00* "
        f"via PIX ({pix_key}) e envie o comprovante aqui no chat. 😊"
    )
    try:
        conv_id = await find_or_create_conversation(PHONE)
        await send_message(conv_id, msg)
        print(f"✅ Confirmação enviada para Sayonara (conv_id={conv_id})")
    except Exception as e:
        print(f"⚠️  Falha ao enviar WhatsApp: {e}")

    # --- 4. Notificar clínica ---
    await _notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Data e horário: 17/06/2026 às 11:00\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: Presencial",
        phone=PHONE,
        subject=f"Agendamento realizado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada.")


if __name__ == "__main__":
    asyncio.run(main())
