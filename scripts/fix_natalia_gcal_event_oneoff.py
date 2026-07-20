"""
One-off: a consulta remarcada da Natalia Camara Lima Alves De Souza Pimentel
(5581996332827, 06/07/2026 07:30, Dra. Bruna) tinha um evento no Google Calendar
com status "cancelled" (evento bb3psfo966q5vqhq0kc4bifpms) — provavelmente
cancelado por engano durante as tentativas de correção manual do bug de
remarcação <24h. Por isso a clínica não conseguia ver a consulta na agenda.

Correção: cria um evento NOVO e ativo no Google Calendar, atualiza o
appointment_id no banco para apontar para ele, e notifica a clínica pelo
fluxo normal de "Agendamento realizado".
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581996332827@s.whatsapp.net"
OLD_APPOINTMENT_ID = "bb3psfo966q5vqhq0kc4bifpms"
PATIENT_NAME = "Natalia Camara Lima Alves De Souza Pimentel"
DOCTOR_LABEL = "Dra. Bruna"
DOCTOR_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
CALENDAR_ID = "brunalima.psiquiatra@gmail.com"
START = datetime(2026, 7, 6, 7, 30, tzinfo=TZ)
DURATION_MIN = 60
MODALITY = "online"
PATIENT_EMAIL = "nataliapimentel81@gmail.com"


async def main():
    from app.database import get_supabase, log_event
    from app.google_calendar import create_event, cancel_event

    client = await get_supabase()

    # 1. Cria evento novo e ativo no Google Calendar
    new_event_id = await create_event(
        calendar_id=CALENDAR_ID,
        start=START,
        slot_minutes=DURATION_MIN,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality=MODALITY,
        patient_email=PATIENT_EMAIL,
        patient_number=PHONE,
    )
    print(f"✅ Evento criado no Google Calendar: {new_event_id}")

    # 2. Tenta remover o evento antigo (cancelado) para não deixar lixo na agenda
    try:
        await cancel_event(CALENDAR_ID, OLD_APPOINTMENT_ID)
        print("✅ Evento antigo (cancelado) removido definitivamente.")
    except Exception as e:
        print(f"⚠️  Não foi possível remover o evento antigo (pode já estar removido): {e}")

    # 3. Atualiza o appointment_id no banco
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", OLD_APPOINTMENT_ID).execute()
    print("✅ appointment_id atualizado no banco.")

    # 4. Notifica a clínica no fluxo normal (mesmo formato de confirm_appointment)
    from app.graph.tools import _notify_clinic, _build_registration_block
    _weekday_labels = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
                        3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"}
    formatted = f"{_weekday_labels[START.weekday()]}, {START.strftime('%d/%m/%Y às %H:%M')}"
    registration_block = (
        "\n\n📋 CADASTRO DO PACIENTE:\n"
        f"  Nome: {PATIENT_NAME}\n"
        f"  Telefone: {PHONE.replace('@s.whatsapp.net', '')}\n"
        f"  E-mail: {PATIENT_EMAIL}"
    )
    await _notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Data e horário: {formatted}\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: Online\n\n"
        f"⚠️ Consulta remarcada de 01/07 para esta data. Nova taxa de reserva de "
        f"R$ 100,00 solicitada (correção de bug de remarcação <24h — a mensagem "
        f"já foi enviada manualmente à paciente)."
        f"{registration_block}",
        phone=PHONE,
        subject=f"Agendamento realizado — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada por e-mail (fluxo normal de agendamento).")

    await log_event("appointment_booked", PHONE, {
        "doctor": "bruna",
        "datetime": START.isoformat(),
        "duration_minutes": DURATION_MIN,
        "patient_name": PATIENT_NAME,
        "session_note": "",
        "note": "correção manual — evento antigo estava cancelled no Google Calendar",
    })
    print("✅ Evento registrado.")


asyncio.run(main())
