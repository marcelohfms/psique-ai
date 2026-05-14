import os
from supabase import AsyncClient, acreate_client

# ── Doctor ID map (from doctors table) ───────────────────────────────────────

DOCTOR_IDS: dict[str, str] = {
    "julio": "d5baa58b-a788-4f40-b8c0-512c189150be",
    "bruna": "18b01f87-eacd-4905-bd4a-a8293991e6fd",
}

DOCTOR_NAMES: dict[str, str] = {v: k for k, v in DOCTOR_IDS.items()}

# ── Supabase client ───────────────────────────────────────────────────────────

_supabase: AsyncClient | None = None


async def get_supabase() -> AsyncClient:
    global _supabase
    if _supabase is None:
        _supabase = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _supabase


# ── User helpers ──────────────────────────────────────────────────────────────

def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


def _phone_variants(phone: str) -> list[str]:
    """Return both the 9-digit and 8-digit variants of a Brazilian mobile number.

    Brazilian mobiles gained a leading 9 in 2012–2016. Chatwoot/Evolution may
    deliver the same number with or without the extra 9, causing duplicate users.
    We normalise to the WITH-9 form (current standard) and also try the legacy form.
    """
    digits = _strip_phone(phone)
    # Must be a Brazilian mobile: 55 + 2-digit DDD + 8 or 9 digits
    if len(digits) == 13 and digits.startswith("55"):
        # Has the 9 already (55 + DDD + 9XXXXXXXX)
        return [digits, digits[:4] + digits[5:]]   # also try without the 9
    if len(digits) == 12 and digits.startswith("55"):
        # Missing the 9 (55 + DDD + 8XXXXXXXX)
        return [digits[:4] + "9" + digits[4:], digits]  # canonical with-9 first
    return [digits]


async def get_users_by_phone(phone: str) -> list[dict]:
    """Return ALL users rows for this phone number (both digit variants)."""
    client = await get_supabase()
    seen_ids: set[str] = set()
    rows: list[dict] = []
    for variant in _phone_variants(phone):
        result = (
            await client.from_("users")
            .select("*")
            .eq("number", variant)
            .execute()
        )
        for row in (result.data or []):
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                rows.append(row)
    return rows


async def get_user_by_phone(phone: str) -> dict | None:
    """Return the users row for this phone number, or None if not found.
    When multiple patients share a phone, returns the active one (or first found).
    """
    rows = await get_users_by_phone(phone)
    if not rows:
        return None
    active = next((r for r in rows if r.get("active")), None)
    return active or rows[0]


async def upsert_user(phone: str, data: dict, user_id: str | None = None) -> None:
    """Insert or update a user record.

    When user_id is given, updates that specific record.
    Otherwise resolves the record by phone: updates if exactly one found,
    inserts if none found, updates the active one if multiple found.
    """
    client = await get_supabase()
    if user_id:
        await client.from_("users").update(data).eq("id", user_id).execute()
        return
    rows = await get_users_by_phone(phone)
    if len(rows) == 0:
        canonical = _phone_variants(phone)[0]
        await client.from_("users").insert({"number": canonical, **data}).execute()
    elif len(rows) == 1:
        await client.from_("users").update(data).eq("id", rows[0]["id"]).execute()
    else:
        # Multiple patients — update the active one to avoid overwriting the wrong record
        target = next((r for r in rows if r.get("active")), rows[0])
        await client.from_("users").update(data).eq("id", target["id"]).execute()


# ── Event tracking ────────────────────────────────────────────────────────────

async def log_event(event_type: str, phone: str, metadata: dict | None = None) -> None:
    """Insert a tracking event. Fire-and-forget — errors are swallowed."""
    try:
        client = await get_supabase()
        await client.from_("events").insert({
            "event_type": event_type,
            "phone": _strip_phone(phone),
            "metadata": metadata or {},
        }).execute()
    except Exception:
        pass  # never let tracking break the main flow


# ── Appointment helpers ───────────────────────────────────────────────────────

async def get_upcoming_appointments(phone: str) -> list[dict]:
    """Return scheduled future appointments for a user, ordered by start_time."""
    client = await get_supabase()
    user = await get_user_by_phone(phone)
    if not user:
        return []
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    result = (
        await client.from_("appointments")
        .select("appointment_id, start_time, end_time, status")
        .eq("user_id", user["id"])
        .eq("status", "scheduled")
        .gte("start_time", now_iso)
        .order("start_time")
        .execute()
    )
    return result.data or []


# ── Message persistence ───────────────────────────────────────────────────────

async def save_message(phone: str, role: str, content: str) -> None:
    """Persist a chat message. Fire-and-forget — errors are swallowed."""
    try:
        client = await get_supabase()
        await client.from_("messages").insert({
            "phone": _strip_phone(phone),
            "role": role,
            "content": content,
        }).execute()
    except Exception:
        pass  # never let persistence break the main flow


# ── LangGraph checkpointer ────────────────────────────────────────────────────
# AsyncPostgresSaver.from_conn_string is an async context manager in v3.x
# Use it directly in the FastAPI lifespan (see main.py)
