import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996332827"
APPOINTMENT_ID = "bb3psfo966q5vqhq0kc4bifpms"
DOCTOR = "bruna"
TZ = ZoneInfo("America/Recife")


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import cancel_event
    from app.chatwoot import find_or_create_conversation, send_message

    client = await get_supabase()
    calendar_id = await _get_doctor_calendar_id(DOCTOR)

    await cancel_event(calendar_id, APPOINTMENT_ID)
    print("✅ Evento removido do Google Calendar")

    now = datetime.now(TZ)
    await client.from_("appointments").update({
        "status": "canceled",
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Status atualizado para canceled (taxa perdida) no banco")

    await log_event("appointment_canceled", PHONE, {
        "appointment_id": APPOINTMENT_ID,
        "preserve_fee": False,
        "reason": "correção manual — remarcação solicitada <24h, mensagem conflitante da Eva",
    })

    await _notify_clinic(
        "Agendamento cancelado (correção manual) ❌\n"
        "Paciente: Natalia Camara Lima Alves De Souza Pimentel\n"
        "Data e horário: 01/07/2026 às 10:00\n"
        "Médico(a): Dra. Bruna\n"
        "Motivo: remarcação solicitada com 14min de antecedência; Eva enviou mensagem "
        "conflitante oferecendo reaproveitar a taxa. Taxa perdida conforme política de 24h. "
        "Paciente será orientada a agendar nova consulta com nova taxa.",
        phone=PHONE,
        subject="Agendamento cancelado — Natalia (correção manual)",
    )
    print("✅ Clínica notificada")

    msg = (
        "Oi, Natalia! Peço desculpas, preciso corrigir uma informação que passei antes — "
        "acabei te enviando duas mensagens conflitantes. 🙏\n\n"
        "Quando você pagou a taxa de reserva, já tínhamos avisado que cancelamentos ou "
        "remarcações com menos de 24h de antecedência não permitem manter a taxa. Como o "
        "pedido foi feito em cima da hora, sua consulta das 10:00 de hoje foi cancelada, e "
        "será necessário agendar uma nova consulta com uma nova taxa de reserva de R$ 100,00.\n\n"
        "Me desculpe pelo transtorno! Para qual dia e turno você prefere agendar a nova "
        "consulta? 😊"
    )
    conv_id = await find_or_create_conversation(f"{PHONE}@s.whatsapp.net")
    await send_message(conv_id, msg)
    print(f"✅ Mensagem de correção enviada (conv_id={conv_id})")


asyncio.run(main())
