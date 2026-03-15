import os
from supabase import AsyncClient, acreate_client
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

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


# ── LangGraph checkpointer ────────────────────────────────────────────────────

async def create_checkpointer() -> AsyncPostgresSaver:
    """
    Creates AsyncPostgresSaver using Supabase shared pooler (transaction mode).
    pipeline=False disables prepared statements required by pgbouncer poolers.
    """
    conn_string = os.environ["SUPABASE_CONNECTION_STRING"]
    checkpointer = await AsyncPostgresSaver.afrom_conn_string(
        conn_string,
        pipeline=False,
    )
    await checkpointer.setup()
    return checkpointer
