import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581997380232"

MSG = """Oi Tiago! Passando para confirmar seu reagendamento.

Sua consulta com o Dr. Júlio está confirmada para o dia 09/07 às 16h. A taxa de reserva que você já pagou foi mantida para esse novo horário — não é necessário pagar de novo (desconsidere qualquer lembrete de cobrança que tenha recebido, foi um erro nosso de sistema).

Só um aviso importante sobre a política de reagendamentos: esse foi o seu único reagendamento com direito a manter a taxa de reserva. A partir de um segundo reagendamento, será necessário solicitar uma nova consulta e pagar uma nova taxa de reserva.

Qualquer dúvida, estou à disposição! 😊"""


async def main():
    from app.whatsapp import send_text
    await send_text(PHONE, MSG)
    print("Mensagem enviada.")

asyncio.run(main())
