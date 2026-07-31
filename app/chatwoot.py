"""
Chatwoot REST API client and phone→conversation_id store.

Environment variables:
  CHATWOOT_BASE_URL         — e.g. https://evolution-chatwoot.5pqooc.easypanel.host
  CHATWOOT_ACCOUNT_ID       — numeric account ID (e.g. "1")
  CHATWOOT_AGENT_BOT_TOKEN  — access token from the Agent Bot settings page
  CHATWOOT_INBOX_ID         — numeric ID of the WhatsApp inbox the bot writes to
"""
import asyncio
import logging
import os
import httpx

logger = logging.getLogger(__name__)

# In-memory map: phone (with @s.whatsapp.net) -> Chatwoot conversation_id
_store: dict[str, int] = {}

# Chatwoot 4.16 tightened rate limits, agent quotas and token access on the
# Application API, so a 429 is now a normal transient condition rather than
# something that never happens. Every call goes through _request() instead of a
# bare raise_for_status(), otherwise a single throttled response silently kills
# the reply (webhook path) or aborts a whole reminder batch (cron path).
_MAX_ATTEMPTS = 3
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 1.0
_MAX_RETRY_DELAY_SECONDS = 60.0


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying: Chatwoot's Retry-After when it sends one,
    exponential backoff otherwise."""
    try:
        raw = response.headers.get("Retry-After")
    except Exception:
        raw = None
    if raw is not None:
        try:
            return max(0.0, min(float(raw), _MAX_RETRY_DELAY_SECONDS))
        except (TypeError, ValueError):
            pass  # Retry-After may be an HTTP-date; fall back to backoff
    return _BACKOFF_BASE_SECONDS * (2 ** attempt)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    tolerate: tuple[int, ...] = (),
    **kwargs,
) -> httpx.Response:
    """Call the Chatwoot API, retrying on 429/5xx before giving up.

    `tolerate` lists status codes that are expected and must not raise (e.g. 404
    when removing an assignment that doesn't exist).
    """
    response = None
    for attempt in range(_MAX_ATTEMPTS):
        response = await getattr(client, method.lower())(url, **kwargs)
        if response.status_code not in _RETRY_STATUSES:
            break
        if attempt == _MAX_ATTEMPTS - 1:
            logger.error(
                "CHATWOOT_API_EXHAUSTED %s %s status=%s attempts=%s",
                method, url, response.status_code, _MAX_ATTEMPTS,
            )
            break
        delay = _retry_delay(response, attempt)
        logger.warning(
            "CHATWOOT_API_RETRY %s %s status=%s attempt=%s delay=%.1fs",
            method, url, response.status_code, attempt + 1, delay,
        )
        await asyncio.sleep(delay)

    if response.status_code not in tolerate:
        response.raise_for_status()
    return response


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


async def send_template_message(
    conversation_id: int,
    template_name: str,
    language: str,
    category: str,
    body_params: dict[str, str],
    content: str = "",
) -> None:
    """Send a WhatsApp template message via Chatwoot (handles both send + Chatwoot record)."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    payload = {
        "content": content,
        "message_type": "outgoing",
        "private": False,
        "template_params": {
            "name": template_name,
            "category": category,
            "language": language,
            "processed_params": {"body": body_params},
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        await _request(client, "POST", url, json=payload, headers=_bot_headers())


async def send_message(conversation_id: int, text: str) -> None:
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    payload = {"content": text, "message_type": "outgoing", "private": False}
    async with httpx.AsyncClient(timeout=10) as client:
        await _request(client, "POST", url, json=payload, headers=_bot_headers())


async def unassign_agent_bot(conversation_id: int) -> None:
    """Remove human agent assignment so agents can take over in Chatwoot. 404 = no agent assigned, safe to ignore."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/assignments"
    async with httpx.AsyncClient(timeout=10) as client:
        await _request(client, "DELETE", url, tolerate=(404,), headers=_bot_headers())


async def add_private_note(conversation_id: int, text: str) -> None:
    """Add a private (internal) note to a Chatwoot conversation, visible only to agents."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    payload = {"content": text, "message_type": "outgoing", "private": True}
    async with httpx.AsyncClient(timeout=10) as client:
        await _request(client, "POST", url, json=payload, headers=_bot_headers())


async def reopen_conversation(conversation_id: int) -> None:
    """Reopen a pending/resolved conversation so it appears in the agent's open queue."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/toggle_status"
    payload = {"status": "open"}
    async with httpx.AsyncClient(timeout=10) as client:
        await _request(client, "POST", url, json=payload, headers=_bot_headers())


async def set_labels(conversation_id: int, add: list[str], remove: list[str] | None = None) -> None:
    """Set labels on a conversation: adds the given labels and removes others atomically."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/labels"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await _request(client, "GET", url, headers=_headers())
        current = set(resp.json().get("payload") or [])
        for label in (remove or []):
            current.discard(label)
        for label in add:
            current.add(label)
        await _request(client, "POST", url, json={"labels": list(current)}, headers=_headers())


async def add_label(conversation_id: int, label: str) -> None:
    """Add a label to a Chatwoot conversation."""
    await set_labels(conversation_id, add=[label])


async def get_last_patient_message(conversation_id: int) -> dict | None:
    """Return the last incoming message from the patient as {"content", "attachments"},
    or None if there isn't one.

    Includes attachment-only messages (empty content, e.g. a payment receipt image
    sent without a caption) — otherwise reactivating Eva via the eva-ativa label can
    never recover them, since there'd be nothing left to reprocess.
    """
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/{conversation_id}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await _request(client, "GET", url, headers=_headers())
        data = resp.json().get("payload") or {}
        if isinstance(data, list):
            messages = data
        elif isinstance(data, dict):
            messages = data.get("messages") or []
        else:
            messages = []
    incoming = [
        m for m in messages
        if m.get("message_type") == 0 and ((m.get("content") or "").strip() or m.get("attachments"))
    ]
    if not incoming:
        return None
    last = max(incoming, key=lambda m: m.get("created_at", 0))
    return {
        "content": (last.get("content") or "").strip(),
        "attachments": last.get("attachments") or [],
    }


# ── Contact / conversation lookup-or-create ───────────────────────────────────


def _phone_variants(digits: str) -> list[str]:
    """Return both 9-digit and 8-digit variants of a Brazilian mobile number."""
    if len(digits) == 13 and digits.startswith("55"):
        return [digits, digits[:4] + digits[5:]]
    if len(digits) == 12 and digits.startswith("55"):
        return [digits[:4] + "9" + digits[4:], digits]
    return [digits]


async def _search_contact(client: httpx.AsyncClient, phone_digits: str) -> dict | None:
    """Return the matching Chatwoot contact for a phone, trying both 9-digit and 8-digit variants.

    A phone number can have a stray duplicate contact (e.g. created once with the
    extra 9 and once without) with no linked conversation. Prefer a contact that
    already has a contact_inbox over an empty duplicate, regardless of which
    variant matched it first.
    """
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/search"
    candidates = []
    for variant in _phone_variants(phone_digits):
        resp = await _request(
            client, "GET", url,
            params={"q": variant, "include": "contact_inboxes"},
            headers=_headers(),
        )
        payload = resp.json().get("payload") or []
        if payload:
            candidates.append(payload[0])
    if not candidates:
        return None
    with_inbox = [c for c in candidates if c.get("contact_inboxes")]
    return (with_inbox or candidates)[0]


async def _create_contact(client: httpx.AsyncClient, phone_digits: str) -> dict:
    """Create a Chatwoot contact for the phone in the configured inbox."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts"
    body = {
        "inbox_id": _inbox_id(),
        "name": phone_digits,
        "phone_number": f"+{phone_digits}",
        "identifier": phone_digits,
    }
    resp = await _request(client, "POST", url, json=body, headers=_headers())
    return resp.json().get("payload", {}).get("contact", {})


async def _get_contact_conversations(client: httpx.AsyncClient, contact_id: int) -> list:
    """Fetch every conversation of a contact, across all inboxes."""
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/contacts/{contact_id}/conversations"
    resp = await _request(client, "GET", url, headers=_headers())
    return resp.json().get("payload") or []


def _pick_conversation(convs: list) -> tuple[int, str] | None:
    """Return (conv_id, status) of the most recent conversation in our inbox, or None."""
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

        # One fetch serves both the inbox_id-mismatch diagnostic log and the pick
        # below — this used to be two identical GETs to the same endpoint.
        all_convs = await _get_contact_conversations(client, contact_id)
        logger.info("FIND_CONV contact_id=%s inbox_id_cfg=%s all_convs=%s",
                    contact_id, _inbox_id(),
                    [(c.get("id"), c.get("inbox_id"), c.get("status")) for c in all_convs])

        result = _pick_conversation(all_convs)
        if result is None:
            raise RuntimeError(f"No Chatwoot conversation found for {digits} and cannot create one for WhatsApp inboxes")
        conv_id, status = result
        if status not in ("open", "pending"):
            await reopen_conversation(conv_id)

    _store[phone] = conv_id
    return conv_id
