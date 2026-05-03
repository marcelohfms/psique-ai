"""
Chatwoot REST API client and phone→conversation_id store.

Environment variables:
  CHATWOOT_BASE_URL         — e.g. https://evolution-chatwoot.5pqooc.easypanel.host
  CHATWOOT_ACCOUNT_ID       — numeric account ID (e.g. "1")
  CHATWOOT_AGENT_BOT_TOKEN  — access token from the Agent Bot settings page
"""
import os
import httpx

# In-memory map: phone (with @s.whatsapp.net) -> Chatwoot conversation_id
_store: dict[str, int] = {}


def register_conversation(phone: str, conversation_id: int) -> None:
    _store[phone] = conversation_id


def get_conversation_id(phone: str) -> int | None:
    return _store.get(phone)


def _base_url() -> str:
    return os.getenv("CHATWOOT_BASE_URL", "").rstrip("/")


def _account_id() -> str:
    return os.getenv("CHATWOOT_ACCOUNT_ID", "1")


def _headers() -> dict:
    return {
        "api_access_token": os.getenv("CHATWOOT_AGENT_BOT_TOKEN", ""),
        "Content-Type": "application/json",
    }


async def send_message(conversation_id: int, text: str) -> None:
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    payload = {"content": text, "message_type": "outgoing", "private": False}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_headers())
        response.raise_for_status()


async def unassign_agent_bot(conversation_id: int) -> None:
    """Remove agent bot assignment so human agents can take over in Chatwoot."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/assignments"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.delete(url, headers=_headers())
        response.raise_for_status()
