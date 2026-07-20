"""
One-off: envia à Lara (5581998696027) a confirmação da taxa de reserva do retorno 13/07 11:00
que a Eva registrou mas não confirmou. NÃO re-registra pagamento (já está na planilha row 254).
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581998696027@s.whatsapp.net"
MSG = (
    "Comprovante recebido e registrado com sucesso! ✅ Sua vaga para o retorno da Lara "
    "com o Dr. Júlio no dia 13/07, às 11:00, está garantida.\n\n"
    "Valor pago: R$ 100,00 — taxa de reserva registrada. O saldo restante para quitação "
    "no dia da consulta é de R$ 600,00 (com desconto para pagamento via PIX ou em dinheiro).\n\n"
    "Se precisar de mais alguma coisa, estou à disposição! 😊"
)

async def main():
    from app.whatsapp import send_text
    await send_text(PHONE, MSG)
    print("✅ Confirmação enviada à Lara.")
    print(MSG)

asyncio.run(main())
