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


async def get_user_by_phone(phone: str) -> dict | None:
    """Return the users row for this phone number, or None if not found."""
    client = await get_supabase()
    result = (
        await client.from_("users")
        .select("*")
        .eq("number", _strip_phone(phone))
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


async def upsert_user(phone: str, data: dict) -> None:
    """Insert or update a user record keyed by phone number."""
    client = await get_supabase()
    payload = {"number": _strip_phone(phone), **data}
    await client.from_("users").upsert(payload, on_conflict="number").execute()


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


# ── LangGraph checkpointer ────────────────────────────────────────────────────
# AsyncPostgresSaver.from_conn_string is an async context manager in v3.x
# Use it directly in the FastAPI lifespan (see main.py)
