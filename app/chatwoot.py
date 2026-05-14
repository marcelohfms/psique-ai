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
    # CHATWOOT_USER_TOKEN is a human-agent API token with contact/search permissions.
    # Falls back to the agent-bot token when not set.
    token = os.getenv("CHATWOOT_USER_TOKEN") or os.getenv("CHATWOOT_AGENT_BOT_TOKEN", "")
    return {
        "api_access_token": token,
        "Content-Type": "application/json",
    }


def _bot_headers() -> dict:
    # Always uses the agent-bot token so outgoing messages appear as sent by the bot,
    # not by the human agent whose token is in CHATWOOT_USER_TOKEN.
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
        response = await client.post(url, json=payload, headers=_bot_headers())
        response.raise_for_status()


async def unassign_agent_bot(conversation_id: int) -> None:
    """Remove human agent assignment so agents can take over in Chatwoot. 404 = no agent assigned, safe to ignore."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/assignments"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.delete(url, headers=_bot_headers())
        if response.status_code != 404:
            response.raise_for_status()


async def add_private_note(conversation_id: int, text: str) -> None:
    """Add a private (internal) note to a Chatwoot conversation, visible only to agents."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    payload = {"content": text, "message_type": "outgoing", "private": True}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_bot_headers())
        response.raise_for_status()


async def reopen_conversation(conversation_id: int) -> None:
    """Reopen a pending/resolved conversation so it appears in the agent's open queue."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/toggle_status"
    payload = {"status": "open"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=_bot_headers())
        response.raise_for_status()


async def set_labels(conversation_id: int, add: list[str], remove: list[str] | None = None) -> None:
    """Set labels on a conversation: adds the given labels and removes others atomically."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/labels"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        current = set(resp.json().get("payload") or [])
        for label in (remove or []):
            current.discard(label)
        for label in add:
            current.add(label)
        response = await client.post(url, json={"labels": list(current)}, headers=_headers())
        response.raise_for_status()


async def add_label(conversation_id: int, label: str) -> None:
    """Add a label to a Chatwoot conversation."""
    await set_labels(conversation_id, add=[label])


async def get_last_patient_message(conversation_id: int) -> str | None:
    """Return the text of the last incoming message from the patient, or None."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        data = resp.json().get("payload") or {}
        if isinstance(data, list):
            messages = data
        elif isinstance(data, dict):
            messages = data.get("messages") or []
        else:
            messages = []
    incoming = [m for m in messages if m.get("message_type") == 0 and (m.get("content") or "").strip()]
    if not incoming:
        return None
    last = max(incoming, key=lambda m: m.get("created_at", 0))
    return last.get("content", "").strip() or None


# ── Contact / conversation lookup-or-create ───────────────────────────────────


def _phone_variants(digits: str) -> list[str]:
    """Return both 9-digit and 8-digit variants of a Brazilian mobile number."""
    if len(digits) == 13 and digits.startswith("55"):
        return [digits, digits[:4] + digits[5:]]
    if len(digits) == 12 and digits.startswith("55"):
        return [digits[:4] + "9" + digits[4:], digits]
    return [digits]


async def _search_contact(client: httpx.AsyncClient, phone_digits: str) -> dict | None:
    """Return the first matching Chatwoot contact for a phone, trying both 9-digit and 8-digit variants."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/search"
    for variant in _phone_variants(phone_digits):
        resp = await client.get(url, params={"q": variant, "include": "contact_inboxes"}, headers=_headers())
        resp.raise_for_status()
        payload = resp.json().get("payload") or []
        if payload:
            return payload[0]
    return None


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


async def _find_conversation_for_contact(client: httpx.AsyncClient, contact_id: int) -> tuple[int, str] | None:
    """Return (conv_id, status) of the most recent conversation for the contact in our inbox, or None."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/{contact_id}/conversations"
    resp = await client.get(url, headers=_headers())
    resp.raise_for_status()
    convs = resp.json().get("payload") or []
    inbox = _inbox_id()
    # Prefer open/pending; fall back to most recent resolved
    open_conv = next((c for c in convs if c.get("inbox_id") == inbox and c.get("status") in ("open", "pending")), None)
    if open_conv:
        return open_conv["id"], open_conv["status"]
    resolved = [c for c in convs if c.get("inbox_id") == inbox]
    if resolved:
        latest = max(resolved, key=lambda c: c.get("id", 0))
        return latest["id"], latest.get("status", "resolved")
    return None


async def find_or_create_conversation(phone: str) -> int:
    """
    Resolve a Chatwoot conversation_id for a WhatsApp phone. Reopens a resolved
    conversation rather than creating a new one (POST /conversations fails for
    WhatsApp inboxes). Caches the result in the in-memory store.
    """
    cached = _store.get(phone)
    if cached is not None:
        return cached

    digits = _strip_phone(phone)
    async with httpx.AsyncClient(timeout=10) as client:
        contact = await _search_contact(client, digits)
        logger.info("FIND_CONV digits=%s contact=%s", digits, contact.get("id") if contact else None)
        if contact is None:
            contact = await _create_contact(client, digits)
        contact_id = contact.get("id")
        if not contact_id:
            raise RuntimeError(f"Chatwoot returned no contact id for {digits}")

        # Log all conversations for this contact to diagnose inbox_id mismatches
        url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/{contact_id}/conversations"
        _dbg = await client.get(url, headers=_headers())
        _all_convs = (_dbg.json().get("payload") or []) if _dbg.status_code == 200 else []
        logger.info("FIND_CONV contact_id=%s inbox_id_cfg=%s all_convs=%s",
                    contact_id, _inbox_id(),
                    [(c.get("id"), c.get("inbox_id"), c.get("status")) for c in _all_convs])

        result = await _find_conversation_for_contact(client, contact_id)
        if result is None:
            raise RuntimeError(f"No Chatwoot conversation found for {digits} and cannot create one for WhatsApp inboxes")
        conv_id, status = result
        if status not in ("open", "pending"):
            await reopen_conversation(conv_id)

    _store[phone] = conv_id
    return conv_id
