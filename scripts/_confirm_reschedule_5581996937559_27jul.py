import asyncio
import httpx
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996937559@s.whatsapp.net"
NOTE = (
    "Confirmado com a Ludmilla: reagendar a consulta do Heitor para "
    "segunda-feira, 27/07/2026, às 10:00, com Dr. Júlio, presencial "
    "(bloco de 2h, primeira consulta). Pode finalizar o reagendamento."
)


async def main():
    from app.chatwoot import find_or_create_conversation, _base_url, _account_id, _headers

    conv_id = await find_or_create_conversation(PHONE)
    print(f"Conversation ID: {conv_id}")

    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conv_id}/messages"
    payload = {"content": NOTE, "message_type": "outgoing", "private": True}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_headers())
        response.raise_for_status()
        print("Nota privada enviada:", response.json().get("id"))

asyncio.run(main())
