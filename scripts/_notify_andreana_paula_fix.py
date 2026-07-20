import asyncio
from dotenv import load_dotenv
load_dotenv()

SUBJECT = "Correção de agendamento — consulta alterada para paciente Andreana Beserra Quidute Sole"

BODY = """\
Olá,

A consulta de segunda-feira, 27/07/2026 às 14:00, com o Dr. Júlio (presencial), estava
registrada por engano em nome de Paula Morgana Quidute Vieira. Foi corrigida e agora está
registrada corretamente para a paciente Andreana Beserra Quidute Sole.

— Eva, assistente virtual Psique
"""


async def main():
    from app.email_sender import send_clinic_notification_email
    await send_clinic_notification_email(SUBJECT, BODY)
    print("E-mail enviado (ou ignorado silenciosamente se SMTP/CLINIC_NOTIFY_EMAIL não configurados).")

asyncio.run(main())
