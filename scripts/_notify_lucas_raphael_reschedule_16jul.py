"""
One-off: avisa Lucas Raphael e a mãe (Silvana Kárin) sobre o reagendamento da
consulta de 13/07/2026 17:00 -> 16/07/2026 18:00 (online, Dr. Júlio, encaixe
em dia de feriado municipal).

Uso: uv run python scripts/_notify_lucas_raphael_reschedule_16jul.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_NAME = "Lucas Raphael"
DOCTOR_LABEL = "Dr. Júlio"

CONTACTS = [
    {"phone": "5581994357788@s.whatsapp.net", "first_name": "Lucas", "is_patient": True},
    {"phone": "5581973460726@s.whatsapp.net", "first_name": "Silvana", "is_patient": False},
]


def build_message(first_name: str, is_patient: bool) -> str:
    subject = f"Sua consulta com o *{DOCTOR_LABEL}*" if is_patient else f"A consulta do {PATIENT_NAME} com o *{DOCTOR_LABEL}*"
    return (
        f"Olá, {first_name}! 😊\n\n"
        f"{subject} foi reagendada para:\n"
        f"📅 *16/07/2026 às 18:00*\n"
        f"💻 Modalidade: *Online*\n\n"
        f"A consulta de hoje (13/07 às 17:00) não vai mais acontecer nesse horário — "
        f"foi movida para esta nova data.\n\n"
        f"Qualquer dúvida, estamos à disposição! 🙏"
    )


async def main():
    from app.chatwoot import find_or_create_conversation, send_message

    for c in CONTACTS:
        msg = build_message(c["first_name"], c["is_patient"])
        try:
            conv_id = await find_or_create_conversation(c["phone"])
            await send_message(conv_id, msg)
            print(f"✅ Mensagem enviada para {c['first_name']} ({c['phone']}) — conv_id={conv_id}")
        except Exception as e:
            print(f"⚠️  Falha ao notificar {c['first_name']} ({c['phone']}): {e}")


asyncio.run(main())
