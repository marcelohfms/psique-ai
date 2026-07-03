"""Cliente mínimo da API do Chatwoot para o painel da atendente.

Autocontido (não importa app/) — só o necessário para mandar uma mensagem
de confirmação na conversa já aberta no iframe. Usa o `conversation_id` que
o próprio Chatwoot entrega no evento postMessage, então não precisa
replicar a busca/criação de contato e conversa que existe em app/chatwoot.py.
"""
import os

import httpx


async def send_confirmation_message(conversation_id: int, text: str) -> None:
    base_url = os.environ["CHATWOOT_BASE_URL"].rstrip("/")
    account_id = os.environ["CHATWOOT_ACCOUNT_ID"]
    url = f"{base_url}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    headers = {
        "api_access_token": os.environ["CHATWOOT_AGENT_BOT_TOKEN"],
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url, json={"content": text, "message_type": "outgoing"}, headers=headers,
        )
        response.raise_for_status()
