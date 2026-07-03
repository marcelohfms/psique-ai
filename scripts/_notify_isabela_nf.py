import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.chatwoot import find_or_create_conversation, send_message
    PHONE = "5581999655881@s.whatsapp.net"
    msg = (
        "Olá! Passando para confirmar: a solicitação da Nota Fiscal da consulta da Isabela, "
        "em nome de João André de Siqueira Campos Arruda, já foi registrada e encaminhada para o setor responsável. ✅\n\n"
        "Em breve ela será enviada para o e-mail joaoandrearruda@gmail.com."
    )
    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Mensagem enviada (conv_id={conv_id})")

asyncio.run(main())
