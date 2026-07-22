import asyncio
import os
from dotenv import load_dotenv
load_dotenv()


async def main():
    from app.email_sender import _send_email

    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    to_email = "dr.juliogouveia@gmail.com"
    subject = "Solicitação da família — contato com psicóloga antes da consulta de Suzi (22/07)"
    body = (
        "Olá, Dr. Júlio!\n\n"
        "A mãe de Suzi Monteiro Viana (Renata Monteiro) pediu, em mais de uma mensagem "
        "(08/07, 20/07 e 21/07), que o senhor entrasse em contato com a nova psicóloga de Suzi, "
        "Bruna Psicóloga, antes da consulta dela.\n\n"
        "A consulta de Suzi está marcada para amanhã, 22/07, às 09h.\n\n"
        "— Eva, assistente virtual Psique"
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _send_email, smtp_host, smtp_port, smtp_user, smtp_password, to_email, subject, body
    )
    print("Email enviado para", to_email)


asyncio.run(main())
