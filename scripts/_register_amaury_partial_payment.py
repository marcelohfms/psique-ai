"""
One-off: register the R$450 partial payment for Amaury Ferreira De Lima Junior's
consultation (appointment vkb26h65pguje8k0q0hqu7te98, Dra. Bruna, 01/07/2026).

Total with PIX discount: R$650. R$100 (booking fee) already paid on 2026-06-26.
Etty (contact/mother) is paying R$450 now via the attendant note flow that got
stuck asking for phone confirmation (see prompts.py fix). Remaining balance
after this payment: R$100.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()


async def main():
    from app.graph.tools import register_payment
    from app.whatsapp import send_text
    from app.database import save_message

    phone = "5581999480798"
    state = {
        "patient_name": "Amaury Ferreira De Lima Júnior",
        "patient_age": 15,
        "preferred_doctor": "bruna",
    }
    config = {"configurable": {"phone": phone}}

    result = await register_payment.coroutine(
        amount="450,00",
        drive_link="",
        state=state,
        config=config,
        patient_name_override="",
        image_description="",
        is_link=False,
        payment_method="",
    )
    print("=== register_payment result ===")
    print(result)

    msg = (
        "Etty, o pagamento de R$ 450,00 da consulta do Amaury foi registrado! ✅\n"
        "Saldo restante: R$ 100,00."
    )
    await send_text(phone, msg)
    await save_message(phone, "assistant", msg)
    print("\n=== mensagem enviada ao WhatsApp ===")
    print(msg)


asyncio.run(main())
