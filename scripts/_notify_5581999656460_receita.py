import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581999656460@s.whatsapp.net"


async def main():
    from app.whatsapp import send_text
    msg = (
        "Oi, Kimmy! Peço desculpas pela demora — tivemos uma falha técnica aqui e sua "
        "mensagem não foi respondida a tempo. 🙏\n\n"
        "Já registrei sua solicitação de receita de Zoloft 100mg (2 caixas) e encaminhei "
        "para o Dr. Júlio. Assim que ele emitir, te enviamos por aqui mesmo. Se precisar "
        "de mais alguma coisa, estou à disposição!"
    )
    await send_text(PHONE, msg)
    print("OK: mensagem enviada.")


asyncio.run(main())
