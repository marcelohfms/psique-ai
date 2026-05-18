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


async def upsert_user(phone: str, data: dict, user_id: str | None = None) -> str | None:
    """Insert or update a user record. Returns the user's ID.

    Resolution order:
    1. If user_id is given → update that specific row.
    2. If data contains patient_name → look for an existing row with the same
       (phone, patient_name). If found → update it (never create a duplicate).
    3. If no rows exist → insert a new row.
    4. Fallback → update the single row, or the active one among multiple rows.
    """
    client = await get_supabase()
    if user_id:
        await client.from_("users").update(data).eq("id", user_id).execute()
        return user_id

    rows = await get_users_by_phone(phone)

    # Try to match an existing row by patient_name to avoid duplicates
    target: dict | None = None
    if rows and "patient_name" in data and data["patient_name"]:
        name_lower = data["patient_name"].lower().strip()
        target = next(
            (r for r in rows
             if (r.get("patient_name") or r.get("name") or "").lower().strip() == name_lower),
            None,
        )

    if target:
        await client.from_("users").update(data).eq("id", target["id"]).execute()
        return target["id"]

    if not rows:
        canonical = _phone_variants(phone)[0]
        result = await client.from_("users").insert({"number": canonical, **data}).execute()
        inserted = (result.data or [{}])[0]
        return inserted.get("id")

    # No name match — update single row or active one (safe fallback)
    fallback = rows[0] if len(rows) == 1 else next((r for r in rows if r.get("active")), rows[0])
    await client.from_("users").update(data).eq("id", fallback["id"]).execute()
    return fallback["id"]


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
