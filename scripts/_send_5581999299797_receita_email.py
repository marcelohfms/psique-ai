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
    subject = "Solicitação de receita — Luiz Guilherme da Rocha Carneiro"
    body = (
        "Olá, Dr. Júlio!\n\n"
        "O paciente Luiz Guilherme da Rocha Carneiro solicitou renovação de receita: "
        "razapina 15mg (medicação não vai durar até a próxima consulta, marcada para 03/08/2026 16h).\n\n"
        "E-mail do responsável: marciamrocha@hotmail.com\n\n"
        "— Eva, assistente virtual Psique"
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _send_email, smtp_host, smtp_port, smtp_user, smtp_password, to_email, subject, body
    )
    print("Email enviado para", to_email)


asyncio.run(main())
