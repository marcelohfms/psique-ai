"""
One-off: avisa os contatos do Pedro Lins De Araújo sobre o reagendamento da
consulta de 20/07/2026 14:00 -> 30/07/2026 14:00 (presencial, Dr. Júlio).

Uso: uv run python scripts/_notify_pedro_lins_reschedule_30jul.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_NAME = "Pedro Lins"
DOCTOR_LABEL = "Dr. Júlio"

CONTACTS = [
    {"phone": "5581998208168@s.whatsapp.net", "first_name": "Liana"},
    {"phone": "5581996647090@s.whatsapp.net", "first_name": "Gabriel"},
    {"phone": "5581999578203@s.whatsapp.net", "first_name": "Dione"},
]


def build_message(first_name: str) -> str:
    return (
        f"Olá, {first_name}! 😊\n\n"
        f"A consulta do {PATIENT_NAME} com o *{DOCTOR_LABEL}* foi reagendada para:\n"
        f"📅 *30/07/2026 às 14:00*\n"
        f"🏥 Modalidade: *Presencial*\n\n"
        f"A consulta anterior (20/07 às 14:00) foi cancelada e substituída por esta nova data, "
        f"sem cobrança de nova taxa de reserva.\n\n"
        f"Qualquer dúvida, estamos à disposição! 🙏"
    )


async def main():
    from app.chatwoot import find_or_create_conversation, send_message

    for c in CONTACTS:
        msg = build_message(c["first_name"])
        try:
            conv_id = await find_or_create_conversation(c["phone"])
            await send_message(conv_id, msg)
            print(f"✅ Mensagem enviada para {c['first_name']} ({c['phone']}) — conv_id={conv_id}")
        except Exception as e:
            print(f"⚠️  Falha ao notificar {c['first_name']} ({c['phone']}): {e}")


asyncio.run(main())
