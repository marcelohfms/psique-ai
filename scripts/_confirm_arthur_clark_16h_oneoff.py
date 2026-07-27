"""One-off: confirma na conversa do paciente (WhatsApp de Arthur Tenório Ribeiro
Clark) que a consulta de hoje 27/07/2026 com Dra. Bruna foi reagendada para 16h.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996503841@s.whatsapp.net"
PATIENT_NAME = "Arthur Tenório Ribeiro Clark"
DOCTOR_LABEL = "Dra. Bruna"
NEW_TIME_LABEL = "hoje (27/07) às 16h"


async def main():
    from app.whatsapp import send_text

    msg = (
        f"Olá, {PATIENT_NAME.split()[0]}! Confirmando por aqui: sua consulta "
        f"com a {DOCTOR_LABEL} foi reagendada para *{NEW_TIME_LABEL}* (online), "
        "meia hora mais cedo que o horário original.\n\n"
        "Qualquer dúvida, estou à disposição!"
    )
    await send_text(PHONE, msg)
    print("✅ Mensagem de confirmação enviada.")

asyncio.run(main())
