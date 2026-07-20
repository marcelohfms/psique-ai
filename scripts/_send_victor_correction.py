import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581995106914@s.whatsapp.net"

MSG = """Oi Maria José! Passando para corrigir uma informação sobre o valor da consulta do Victor.

Em maio, quando você nos procurou pela primeira vez, já tínhamos avisado que o valor sofreria reajuste e passaria a ser R$ 850,00 (R$ 800,00 no pagamento à vista/PIX) para a 1ª consulta com o Dr. Júlio — e foi esse reajuste que passou a valer.

Por isso, o saldo restante correto (após a taxa de reserva de R$ 100,00) é R$ 700,00 no pagamento à vista/PIX, e não R$ 600,00 como informei antes. Peço desculpas pelo engano!

As próximas consultas de acompanhamento do Victor ficarão R$ 750,00 (R$ 700,00 no PIX).

Qualquer dúvida, estou à disposição. 😊"""

async def main():
    from app.whatsapp import send_text
    await send_text(PHONE, MSG)
    print("Mensagem enviada.")

asyncio.run(main())
