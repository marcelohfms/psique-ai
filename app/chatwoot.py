"""
Chatwoot REST API client and phone→conversation_id store.

Environment variables:
  CHATWOOT_BASE_URL         — e.g. https://evolution-chatwoot.5pqooc.easypanel.host
  CHATWOOT_ACCOUNT_ID       — numeric account ID (e.g. "1")
  CHATWOOT_AGENT_BOT_TOKEN  — access token from the Agent Bot settings page
  CHATWOOT_INBOX_ID         — numeric ID of the WhatsApp inbox the bot writes to
"""
import logging
import os
import httpx

logger = logging.getLogger(__name__)

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


def _inbox_id() -> int:
    return int(os.getenv("CHATWOOT_INBOX_ID", "0"))


def _headers() -> dict:
    return {
        "api_access_token": os.getenv("CHATWOOT_AGENT_BOT_TOKEN", ""),
        "Content-Type": "application/json",
    }


def _strip_phone(phone: str) -> str:
    """Return the bare digits (no @s.whatsapp.net suffix, no leading +)."""
    return phone.split("@", 1)[0].lstrip("+")


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


async def add_private_note(conversation_id: int, text: str) -> None:
    """Add a private (internal) note to a Chatwoot conversation, visible only to agents."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    payload = {"content": text, "message_type": "outgoing", "private": True}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_headers())
        response.raise_for_status()


async def reopen_conversation(conversation_id: int) -> None:
    """Reopen a pending/resolved conversation so it appears in the agent's open queue."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/toggle_status"
    payload = {"status": "open"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_headers())
        response.raise_for_status()


async def add_label(conversation_id: int, label: str) -> None:
    """Add a label to a Chatwoot conversation."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/labels"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        current = resp.json().get("payload") or []
        if label not in current:
            current.append(label)
            response = await client.post(url, json={"labels": current}, headers=_headers())
            response.raise_for_status()


async def get_last_patient_message(conversation_id: int) -> str | None:
    """Return the text of the last incoming message from the patient, or None."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        messages = resp.json().get("payload", {}).get("messages") or []
    incoming = [m for m in messages if m.get("message_type") == 0 and (m.get("content") or "").strip()]
    if not incoming:
        return None
    last = max(incoming, key=lambda m: m.get("created_at", 0))
    return last.get("content", "").strip() or None


# ── Contact / conversation lookup-or-create ───────────────────────────────────


async def _search_contact(client: httpx.AsyncClient, phone_digits: str) -> dict | None:
    """Return the first matching Chatwoot contact for a phone, or None."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/search"
    resp = await client.get(url, params={"q": phone_digits, "include": "contact_inboxes"}, headers=_headers())
    resp.raise_for_status()
    payload = resp.json().get("payload") or []
    return payload[0] if payload else None


async def _create_contact(client: httpx.AsyncClient, phone_digits: str) -> dict:
    """Create a Chatwoot contact for the phone in the configured inbox."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts"
    body = {
        "inbox_id": _inbox_id(),
        "name": phone_digits,
        "phone_number": f"+{phone_digits}",
        "identifier": phone_digits,
    }
    resp = await client.post(url, json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json().get("payload", {}).get("contact", {})


async def _open_conversation_for_contact(client: httpx.AsyncClient, contact_id: int) -> int | None:
    """Return the id of an open/pending conversation for the contact in our inbox, if any."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/{contact_id}/conversations"
    resp = await client.get(url, headers=_headers())
    resp.raise_for_status()
    convs = resp.json().get("payload") or []
    inbox = _inbox_id()
    for c in convs:
        if c.get("inbox_id") == inbox and c.get("status") in ("open", "pending"):
            return c.get("id")
    return None


async def _create_conversation(client: httpx.AsyncClient, contact_id: int, phone_digits: str) -> int:
    """Create a new conversation for the contact in the configured inbox."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations"
    body = {
        "source_id": phone_digits,
        "inbox_id": _inbox_id(),
        "contact_id": contact_id,
    }
    resp = await client.post(url, json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json().get("id")


async def find_or_create_conversation(phone: str) -> int:
    """
    Resolve a Chatwoot conversation_id for a WhatsApp phone, creating contact
    and conversation if needed. Caches the result in the in-memory store.
    """
    cached = _store.get(phone)
    if cached is not None:
        return cached

    digits = _strip_phone(phone)
    async with httpx.AsyncClient(timeout=10) as client:
        contact = await _search_contact(client, digits)
        if contact is None:
            contact = await _create_contact(client, digits)
        contact_id = contact.get("id")
        if not contact_id:
            raise RuntimeError(f"Chatwoot returned no contact id for {digits}")

        conv_id = await _open_conversation_for_contact(client, contact_id)
        if conv_id is None:
            conv_id = await _create_conversation(client, contact_id, digits)

    _store[phone] = conv_id
    return conv_id
