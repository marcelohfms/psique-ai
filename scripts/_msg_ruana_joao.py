import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from app.chatwoot import find_or_create_conversation, send_message
    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    # Limpa patient_name
    client = await get_supabase()
    await client.from_("users").update({
        "patient_name": "João Gabriel Damásio dos Santos Bione"
    }).eq("id", "95586b6b-d08e-4e5e-be0e-813f937b661f").execute()
    print("✅ patient_name limpo")

    # Verifica outros horários disponíveis no 16/06
    calendar_id = await _get_doctor_calendar_id("julio")
    slots = await get_available_slots(calendar_id, "16/06", "tarde", 60, "julio")
    available = [(s.astimezone(TZ).strftime("%H:%M"), m) for s, m in slots if s.astimezone(TZ).hour != 16]
    print(f"Slots disponíveis 16/06 tarde (exceto 16h): {available}")

    PHONE = "558188731986@s.whatsapp.net"
    msg = (
        "Olá, Ruana! 😊\n\n"
        "Gostaríamos de informar que o horário das 16:00 do dia 16/06 infelizmente acabou sendo "
        "ocupado por outro paciente enquanto aguardávamos sua confirmação.\n\n"
        "Para o dia 16/06 à tarde ainda temos disponível:\n"
        "🕐 *13:00* (presencial ou online)\n\n"
        "Se preferir outro dia ou turno, é só me dizer que verifico as opções!\n\n"
        "Enquanto isso, para finalizar o cadastro do João Gabriel, precisamos de mais algumas informações:\n\n"
        "📝 *Seu nome completo* (responsável)\n"
        "👥 *Grau de parentesco* com o João Gabriel\n"
        "📋 *Seu CPF* (responsável)\n"
        "📧 *E-mail* para envio do Termo de Compromisso e documentos da consulta\n"
        "🏥 *João Gabriel já foi atendido na Clínica Psique antes?*\n\n"
        "Assim que tiver essas informações, já confirmo o agendamento! 🩷"
    )

    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Mensagem enviada (conv_id={conv_id})")

asyncio.run(main())
