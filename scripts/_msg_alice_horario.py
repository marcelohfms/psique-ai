import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.chatwoot import find_or_create_conversation, send_message
    PHONE = "5587981054742@s.whatsapp.net"

    msg = (
        "Olá, Angélica! 😊\n\n"
        "Gostaríamos de nos desculpar pelo transtorno. Como a consulta da Maria Alice tinha sido "
        "cancelada automaticamente pela falta de pagamento da taxa de reserva dentro do prazo, "
        "o horário das 10:00 acabou sendo ocupado por outro paciente.\n\n"
        "Temos disponibilidade no dia *22/06* nos seguintes horários com o *Dr. Júlio*, presencialmente:\n\n"
        "🕘 *09:00*\n"
        "🕚 *11:00*\n\n"
        "Qual desses horários seria melhor para vocês? 🙏"
    )

    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Mensagem enviada (conv_id={conv_id})")

asyncio.run(main())
