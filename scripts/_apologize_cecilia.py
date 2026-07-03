import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.chatwoot import find_or_create_conversation, send_message
    PHONE = "5581991520435@s.whatsapp.net"
    msg = (
        "Olá, Maria! Pedimos desculpas pelas mensagens duplicadas. 🙏\n\n"
        "Houve um problema técnico no nosso sistema que gerou um agendamento indevido para o dia 25/06 — "
        "esse horário já foi cancelado e a cobrança referente a ele não é válida.\n\n"
        "A sua consulta confirmada é:\n"
        "📅 *Terça-feira, 16/06/2026 às 14:00*\n"
        "👨‍⚕️ Dr. Júlio\n"
        "📍 Presencial\n\n"
        "O pagamento da taxa de reserva de *R$ 100,00* se refere apenas a essa consulta. "
        "Pedimos desculpas pelo transtorno! 🩷"
    )
    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Mensagem enviada (conv_id={conv_id})")

asyncio.run(main())
