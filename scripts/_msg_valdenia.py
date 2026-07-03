import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.chatwoot import find_or_create_conversation, send_message
    PHONE = "5581981139373@s.whatsapp.net"

    msg = (
        "Olá! 😊 Comprovante recebido, obrigada!\n\n"
        "Para finalizar o cadastro da *Maria José Alves de Farias* e garantir tudo certinho para a consulta, "
        "precisamos de mais algumas informações:\n\n"
        "📅 *Data de nascimento* da Maria José\n"
        "📋 *CPF* da Maria José\n"
        "📧 *E-mail* para envio do Termo de Compromisso e documentações relacionadas à consulta\n"
        "👨‍👩‍👧 *Grau de parentesco* entre você e a Maria Eduarda\n\n"
        "Assim que tiver esses dados, deixamos o cadastro completo! 🩷"
    )

    conv_id = await find_or_create_conversation(PHONE)
    await send_message(conv_id, msg)
    print(f"✅ Mensagem enviada (conv_id={conv_id})")

asyncio.run(main())
