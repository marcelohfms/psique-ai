import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()


from fastapi import FastAPI, Request, Response, Header, HTTPException
from langchain_core.messages import HumanMessage

from app.graph import graph as graph_module
from app.database import get_user_by_phone, log_event, DOCTOR_NAMES, save_message
from app.buffer import push as buffer_push
from app.auth import router as auth_router
from app.whatsapp import send_text

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Supabase checkpointer on startup if connection string is set."""
    conn_string = os.getenv("SUPABASE_CONNECTION_STRING")

    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph

        logger.info("Connecting to Supabase checkpointer...")
        async with await AsyncConnection.connect(
            conn_string,
            autocommit=True,
            prepare_threshold=None,
            row_factory=dict_row,
        ) as conn:
            checkpointer = AsyncPostgresSaver(conn)
            await checkpointer.setup()
            graph_module.chatbot = build_graph(checkpointer=checkpointer)
            logger.info("Supabase checkpointer ready.")
            yield
    else:
        logger.warning("SUPABASE_CONNECTION_STRING not set — using in-memory checkpointer.")
        yield


app = FastAPI(title="Psique Chatbot", lifespan=lifespan)
app.include_router(auth_router)


# ── Webhook verification (Meta requires GET on the webhook URL) ───────────────

@app.get("/webhook")
async def webhook_verify(request: Request):
    """Meta webhook verification handshake."""
    params = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == os.getenv("WHATSAPP_VERIFY_TOKEN", ""):
        logger.info("Webhook verified by Meta.")
        return Response(content=challenge, media_type="text/plain")

    logger.warning("Webhook verification failed — token mismatch.")
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Message extraction from Meta Cloud API payload ────────────────────────────

async def extract_message(payload: dict) -> tuple[str, str] | None:
    """
    Extract (phone, text) from a Meta Cloud API webhook payload.
    Handles text, audio and image messages.
    Returns None for statuses, reactions, stickers and unsupported types.
    """
    from app.media import process_media

    try:
        entry   = payload["entry"][0]
        change  = entry["changes"][0]
        value   = change["value"]
    except (KeyError, IndexError):
        return None

    # Ignore status updates (delivery receipts etc.)
    if "statuses" in value and "messages" not in value:
        return None

    messages = value.get("messages")
    if not messages:
        return None

    msg = messages[0]
    msg_type = msg.get("type", "")
    from_number = msg.get("from", "")
    if not from_number:
        return None

    phone = from_number + "@s.whatsapp.net"

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "").strip()
        if not text:
            return None
        # Prepend quoted context if this is a reply
        context = msg.get("context")
        if context:
            quoted_body = context.get("quoted_message", {}).get("text", {}).get("body", "")
            if quoted_body:
                text = f'[Em resposta a: "{quoted_body}"]\n{text}'
        return phone, text

    if msg_type == "audio":
        media_id = msg.get("audio", {}).get("id", "")
        if not media_id:
            return None
        text = await process_media(media_id, "audio", phone=phone)
        if not text:
            return None
        return phone, text

    if msg_type == "image":
        media_id = msg.get("image", {}).get("id", "")
        if not media_id:
            return None
        text = await process_media(media_id, "image", phone=phone)
        if not text:
            return None
        return phone, text

    # reaction, sticker, document, location, etc. — ignore
    logger.info("Unsupported message type ignored: %s", msg_type)
    return None


# ── Attendant pause/resume commands ──────────────────────────────────────────
# With Meta Cloud API, messages typed in the WhatsApp app are NOT delivered
# to the webhook. Attendant commands are handled via these admin endpoints.

_HOLD_HOURS = 24


async def _pause_bot_for_patient(phone: str) -> None:
    from app.database import upsert_user
    await upsert_user(phone, {
        "active": False,
        "deactivated_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info("Bot pausado para %s", phone)


async def _resume_bot_for_patient(phone: str) -> None:
    from app.database import upsert_user
    await upsert_user(phone, {
        "active": True,
        "deactivated_at": None,
    })
    logger.info("Bot reativado para %s", phone)


def _check_admin_secret(x_admin_secret: str | None) -> None:
    secret = os.getenv("ADMIN_SECRET", "")
    if not secret or x_admin_secret != secret:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/admin/pause")
async def admin_pause(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Pause the bot for a patient. Body: {"phone": "5583..."}"""
    _check_admin_secret(x_admin_secret)
    body = await request.json()
    phone = body.get("phone", "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    if not phone.endswith("@s.whatsapp.net"):
        phone += "@s.whatsapp.net"
    await _pause_bot_for_patient(phone)
    return {"status": "paused", "phone": phone}


@app.post("/admin/resume")
async def admin_resume(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Resume the bot for a patient. Body: {"phone": "5583..."}"""
    _check_admin_secret(x_admin_secret)
    body = await request.json()
    phone = body.get("phone", "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    if not phone.endswith("@s.whatsapp.net"):
        phone += "@s.whatsapp.net"
    await _resume_bot_for_patient(phone)
    return {"status": "resumed", "phone": phone}


# ── Core message processing ───────────────────────────────────────────────────

async def process_message(phone: str, text: str) -> None:
    """Route a (possibly debounced) message through the LangGraph chatbot."""
    config = {"configurable": {"thread_id": phone, "phone": phone}}

    existing = await get_user_by_phone(phone)
    if existing and existing.get("manual_hold"):
        return  # permanent hold — never reactivates
    if existing and existing.get("active") is False:
        deactivated_at = existing.get("deactivated_at")
        if deactivated_at:
            dt = datetime.fromisoformat(deactivated_at)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - dt < timedelta(hours=_HOLD_HOURS):
                return  # still within 24-h hold
            # 24 h elapsed — reactivate automatically
            from app.database import upsert_user
            await upsert_user(phone, {"active": True, "deactivated_at": None})
            logger.info("Bot auto-reativado para %s após 24 h", phone)
        else:
            return  # no timestamp = permanent hold

    snapshot = await graph_module.chatbot.aget_state(config)
    if snapshot.values:
        state_update = {"messages": [HumanMessage(content=text)]}
    else:
        await log_event("conversation_started", phone)
        _REQUIRED = ("name", "patient_name", "age", "is_patient", "birth_date", "email")
        user_known = existing and all(existing.get(f) is not None for f in _REQUIRED)

        if user_known:
            stage = "patient_agent"
            doctor_key = DOCTOR_NAMES.get(existing["doctor_id"])
        else:
            stage = "collect_info"
            doctor_key = None

        state_update = {
            "messages": [HumanMessage(content=text)],
            "phone": phone,
            "stage": stage,
            "user_name": existing.get("name") if existing else None,
            "is_for_self": None,
            "patient_name": existing.get("patient_name") if existing else None,
            "patient_age": existing.get("age") if existing else None,
            "birth_date": existing.get("birth_date") if existing else None,
            "guardian_name": existing.get("guardian_name") if existing else None,
            "guardian_cpf": existing.get("guardian_cpf") if existing else None,
            "is_patient": existing.get("is_patient") if existing else None,
            "preferred_doctor": doctor_key,
            "patient_email": existing.get("email") if existing else None,
            "consultation_reason": existing.get("consultation_reason") if existing else None,
            "referral_professional": existing.get("referral_professional") if existing else None,
        }

    config["metadata"] = {
        "langfuse_user_id": phone,
        "langfuse_session_id": phone,
    }
    config["tags"] = ["whatsapp", "production"]
    await graph_module.chatbot.ainvoke(state_update, config=config)


async def _reset_conversation(phone: str) -> None:
    """Apaga todo o histórico e estado da conversa para um número."""
    from app.database import get_supabase, _strip_phone
    client = await get_supabase()
    stripped = _strip_phone(phone)

    await client.from_("messages").delete().eq("phone", stripped).execute()
    await client.from_("users").delete().eq("number", stripped).execute()
    for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
        try:
            await client.from_(table).delete().eq("thread_id", phone).execute()
        except Exception:
            pass

    await send_text(phone, "✅ Histórico apagado! Pode começar uma nova conversa.")


async def _handle_payload(payload: dict) -> None:
    try:
        result = await extract_message(payload)
        if result is None:
            return
        phone, text = result

        logger.info("Incoming message from %s: %.80s", phone, text)

        # /reset command
        if text.strip().lower() == "/reset":
            await _reset_conversation(phone)
            return

        await save_message(phone, "user", text)
        await buffer_push(phone, text, process_message)
    except Exception:
        logger.exception("Error handling webhook payload")


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    logger.debug("Webhook payload: %s", payload)
    asyncio.create_task(_handle_payload(payload))
    return {"status": "ok"}


# ── Chatwoot Agent Bot webhook ─────────────────────────────────────────────────

def _extract_chatwoot_message(payload: dict) -> tuple[str, str, int] | None:
    """
    Extract (phone, text, conversation_id) from a Chatwoot Agent Bot webhook.
    Returns None for outgoing/activity messages or missing content.
    message_type: 0=incoming, 1=outgoing, 2=activity
    """
    if payload.get("message_type") not in (0, "incoming"):
        return None
    content = (payload.get("content") or "").strip()
    if not content:
        return None
    conversation = payload.get("conversation", {})
    conversation_id = conversation.get("id")
    if not conversation_id:
        return None
    phone_raw = (
        payload.get("sender", {}).get("phone_number")
        or conversation.get("meta", {}).get("sender", {}).get("phone_number")
        or ""
    ).strip()
    if not phone_raw:
        return None
    phone = phone_raw.lstrip("+") + "@s.whatsapp.net"
    return phone, content, conversation_id


async def _handle_chatwoot_payload(payload: dict) -> None:
    try:
        result = _extract_chatwoot_message(payload)
        if result is None:
            print("CHATWOOT: _extract_chatwoot_message returned None", flush=True)
            return
        phone, text, conversation_id = result
        print(f"CHATWOOT: extracted phone={phone} conv={conversation_id} text={text!r}", flush=True)

        from app.chatwoot import register_conversation
        register_conversation(phone, conversation_id)

        logger.info("Chatwoot message from %s (conv=%s): %.80s", phone, conversation_id, text)

        if text.strip().lower() == "/reset":
            await _reset_conversation(phone)
            return

        await save_message(phone, "user", text)
        await buffer_push(phone, text, process_message)
    except Exception:
        logger.exception("Error handling Chatwoot webhook payload")


@app.post("/chatwoot-webhook")
async def chatwoot_webhook(request: Request):
    payload = await request.json()
    logger.debug("Chatwoot webhook payload: %s", payload)
    asyncio.create_task(_handle_chatwoot_payload(payload))
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
