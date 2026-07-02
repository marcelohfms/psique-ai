#!/usr/bin/env python3
"""Notify Gabriel that the duplicate patient issue has been resolved."""
import asyncio
from app.chatwoot import find_or_create_conversation, send_message

async def main():
    phone = "5581996647090"

    message = """✅ Erro Corrigido

Olá! Identificamos e corrigimos um problema no sistema de agendamentos que afetava seu cadastro.

O problema foi resolvido e seu contato agora está funcionando normalmente para agendar consultas de João Pedro.

Se tiver qualquer dúvida, nos contacte! 😊"""

    print(f"Enviando mensagem para {phone}...")
    try:
        print("1️⃣ Criando/encontrando conversa...")
        conversation_id = await find_or_create_conversation(phone)
        print(f"   Conversation ID: {conversation_id}")

        print("2️⃣ Enviando mensagem...")
        await send_message(conversation_id, message)

        print(f"✅ Mensagem enviada com sucesso!")
    except Exception as e:
        import traceback
        print(f"❌ Erro: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
