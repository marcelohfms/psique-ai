import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581987652022@s.whatsapp.net"

MSG = """Oi Ana Cláudia! Passando para corrigir os horários disponíveis do Dr. Júlio em julho — os que te passei antes estavam incorretos, peço desculpas! Seguem os horários reais a partir de hoje:

📅 **Segunda, 20/07**
• 14h

📅 **Quarta, 22/07**
• 9h

📅 **Quinta, 23/07**
• 10h
• 11h
• 17h

📅 **Segunda, 27/07**
• 9h
• 10h
• 11h
• 14h
• 15h
• 16h
• 17h

📅 **Quarta, 29/07**
• 9h
• 10h
• 11h

📅 **Quinta, 30/07**
• 9h
• 10h
• 11h
• 14h
• 16h

Cada consulta tem duração de 1 hora.

⚠️ Lembrando que nossos agendamentos acontecem simultaneamente, então não conseguimos garantir a disponibilidade por muito tempo — só teremos certeza quando o horário for efetivamente agendado.

Qual horário fica melhor para o Heitor? 💙"""

async def main():
    from app.whatsapp import send_text
    await send_text(PHONE, MSG)
    print("Mensagem enviada.")

asyncio.run(main())
