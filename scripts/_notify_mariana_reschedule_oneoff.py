"""One-off: notifica Mariana Melo Gadelha (5581993062020) sobre a correção do
agendamento — consulta confirmada para 03/08/2026 às 10:00 com Dr. Júlio,
taxa de reserva (já paga em 21/07) permanece válida, nada a pagar.

Usa app.whatsapp.send_text (Chatwoot) em vez do chamado direto ao chatwoot.send_message
para passar pelo guard de sanitização de endereço/fatos da clínica.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581993062020"


async def main():
    from app.whatsapp import send_text

    message = (
        "Olá, Mariana! Tudo bem? 🩷\n\n"
        "Passando para confirmar: sua consulta com o Dr. Júlio foi remarcada e "
        "ficou confirmada para:\n\n"
        "📅 *Segunda-feira, 03/08/2026 às 10:00*\n"
        "👨‍⚕️ Dr. Júlio\n"
        "📍 Presencial\n\n"
        "A taxa de reserva de R$ 100,00 que você já pagou segue válida para essa "
        "consulta — está tudo certo, não é necessário pagar novamente. 😊"
    )
    await send_text(PHONE, message)
    print(f"✅ Mensagem enviada para {PHONE}")

asyncio.run(main())
