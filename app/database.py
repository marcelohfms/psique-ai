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

    Shared fields (active, deactivated_at, price_adjustment_notified_at, manual_hold)
    are always propagated to ALL rows with the same phone number when a contact
    has multiple patients linked.
    """
    # Fields that must stay in sync across all records for the same phone number
    _SHARED_FIELDS = {"active", "deactivated_at", "price_adjustment_notified_at", "manual_hold"}

    client = await get_supabase()
    if user_id:
        await client.from_("users").update(data).eq("id", user_id).execute()
        shared = {k: v for k, v in data.items() if k in _SHARED_FIELDS}
        if shared:
            # Propagate shared fields to all other rows with the same phone
            rows = await get_users_by_phone(phone)
            other_ids = [r["id"] for r in rows if r["id"] != user_id]
            for oid in other_ids:
                await client.from_("users").update(shared).eq("id", oid).execute()
        return user_id

    rows = await get_users_by_phone(phone)

    # Try to match an existing row by patient_name or name to avoid duplicates.
    # Covers both cases: when patient_name is already known, and when only the
    # contact name (name) is available (e.g. early in the collect_info flow).
    target: dict | None = None
    name_to_match = (
        (data.get("patient_name") or "").strip()
        or (data.get("name") or "").strip()
    ).lower()
    if rows and name_to_match:
        target = next(
            (r for r in rows
             if (r.get("patient_name") or r.get("name") or "").lower().strip() == name_to_match),
            None,
        )

    if target:
        await client.from_("users").update(data).eq("id", target["id"]).execute()
        shared = {k: v for k, v in data.items() if k in _SHARED_FIELDS}
        if shared:
            other_ids = [r["id"] for r in rows if r["id"] != target["id"]]
            for oid in other_ids:
                await client.from_("users").update(shared).eq("id", oid).execute()
        return target["id"]

    if not rows:
        canonical = _phone_variants(phone)[0]
        result = await client.from_("users").insert({"number": canonical, **data}).execute()
        inserted = (result.data or [{}])[0]
        return inserted.get("id")

    # No name match — update single row or active one (safe fallback)
    fallback = rows[0] if len(rows) == 1 else next((r for r in rows if r.get("active")), rows[0])
    await client.from_("users").update(data).eq("id", fallback["id"]).execute()
    shared = {k: v for k, v in data.items() if k in _SHARED_FIELDS}
    if shared:
        other_ids = [r["id"] for r in rows if r["id"] != fallback["id"]]
        for oid in other_ids:
            await client.from_("users").update(shared).eq("id", oid).execute()
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


# ── Registration completeness check ──────────────────────────────────────────

def is_registration_complete(user: dict) -> bool:
    """Return True only when the user record has all fields required to proceed
    to scheduling.

    Required for ALL patients:
    - name          (contact name — used to address the person in chat)
    - email         (for documents and termo de compromisso)
    - birth_date    (to determine age / consultation type)
    - doctor_id     (preferred doctor)
    - is_patient    (True/False, not None)
    - is_returning_patient (True/False, not None)

    Additional requirements for MINORS (age < 18):
    - guardian_name
    - guardian_cpf
    - guardian_relationship

    Note: when is_patient=False the contact is NOT the patient.
    In that case `name` is the contact's name and `patient_name` is the patient's.
    Both must be present and different.
    """
    if not user:
        return False

    # Universal required fields
    required = ["name", "email", "birth_date", "doctor_id"]
    for field in required:
        if not user.get(field):
            return False

    if user.get("is_patient") is None:
        return False
    if user.get("is_returning_patient") is None:
        return False

    # When contact ≠ patient, patient_name must be explicitly set
    if user.get("is_patient") is False:
        if not user.get("patient_name"):
            return False

    # Minor-specific requirements
    age = user.get("age")
    if age is not None and age < 18:
        for field in ("guardian_name", "guardian_cpf", "guardian_relationship"):
            if not user.get(field):
                return False

    return True


# ── Appointment helpers ───────────────────────────────────────────────────────

async def get_upcoming_appointments(phone: str) -> list[dict]:
    """Return scheduled/ongoing/recent appointments for a user, ordered by start_time.

    Includes:
    - Future appointments (end_time >= now)
    - Appointments that ended in the last 48 h but are still marked 'scheduled'
      (complete_appointments script hasn't run yet) — flagged with 'recently_ended'
    """
    client = await get_supabase()
    user = await get_user_by_phone(phone)
    if not user:
        return []
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cutoff_recent = (now - timedelta(hours=48)).isoformat()

    # Future + ongoing (end_time has not yet passed)
    future_result = (
        await client.from_("appointments")
        .select("appointment_id, start_time, end_time, status, reschedule_requested_at")
        .in_("status", ["scheduled", "pending_reschedule"])
        .eq("user_id", user["id"])
        .gte("end_time", now_iso)
        .order("start_time")
        .execute()
    )

    # Recently ended but not yet marked as completed (end_time in last 48 h)
    recent_result = (
        await client.from_("appointments")
        .select("appointment_id, start_time, end_time, status, reschedule_requested_at")
        .in_("status", ["scheduled", "pending_reschedule"])
        .eq("user_id", user["id"])
        .lt("end_time", now_iso)
        .gte("end_time", cutoff_recent)
        .order("start_time")
        .execute()
    )

    # Tag recently-ended rows so the caller can present them differently
    recent = [dict(r, recently_ended=True) for r in (recent_result.data or [])]
    return (future_result.data or []) + recent


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


async def get_last_assistant_message_time(phone: str):
    """Return the created_at datetime of the most recent assistant message, or None."""
    try:
        client = await get_supabase()
        result = (
            await client.from_("messages")
            .select("created_at")
            .eq("phone", _strip_phone(phone))
            .eq("role", "assistant")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            from datetime import datetime, timezone
            ts = result.data[0]["created_at"]
            if isinstance(ts, str):
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return ts
    except Exception:
        pass
    return None


# ── LangGraph checkpointer ────────────────────────────────────────────────────
# AsyncPostgresSaver.from_conn_string is an async context manager in v3.x
# Use it directly in the FastAPI lifespan (see main.py)
