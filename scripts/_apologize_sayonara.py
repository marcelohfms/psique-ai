import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.chatwoot import find_or_create_conversation, send_message
    PHONE = "558195302944@s.whatsapp.net"
    msg = (
        "Sayonara, quero me desculpar pela confusão que aconteceu hoje. 🙏\n\n"
        "Houve um problema técnico no nosso sistema que fez com que eu não reconhecesse "
        "seu comprovante de pagamento e informasse incorretamente que o horário tinha sido "
        "ocupado por outro paciente — o que não era verdade. A vaga era sua o tempo todo.\n\n"
        "Já registramos seu pagamento e sua consulta está confirmada e garantida:\n"
        "📅 *Quarta-feira, 17/06/2026 às 11:00*\n"
        "👩‍⚕️ Dra. Bruna\n"
        "📍 Presencial\n\n"
        "Lamentamos muito o transtorno, especialmente sabendo que você se programa vindo de Caruaru. "
        "Estamos aqui para qualquer dúvida! 😊🩷"
    )
    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Pedido de desculpas enviado (conv_id={conv_id})")

asyncio.run(main())
