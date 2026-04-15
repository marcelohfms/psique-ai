import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()


from fastapi import FastAPI, Request
from langchain_core.messages import HumanMessage

from app.graph import graph as graph_module
from app.database import get_user_by_phone, log_event, DOCTOR_NAMES, save_message
from app.buffer import push as buffer_push
from app.auth import router as auth_router
from app.uazapi import send_text

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
        # prepare_threshold=None disables prepared statements, required for
        # pgbouncer in transaction mode (Supabase shared pooler)
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


_TEXT_TYPES = {"Conversation", "ExtendedTextMessage"}
_MEDIA_TYPES = {"AudioMessage", "ImageMessage"}


async def extract_message(payload: dict) -> tuple[str, str] | None:
    """
    Extract (phone, text) from a UAZAPI webhook payload.
    Handles text, extended text, audio (Whisper) and image (GPT-4o vision).
    """
    from app.media import process_media

    msg = payload.get("message", {})

    if msg.get("fromMe") or msg.get("wasSentByApi"):
        return None
    if msg.get("isGroup"):
        return None

    phone = msg.get("chatid", "")
    if not phone:
        return None

    msg_type = msg.get("messageType", "")

    if msg_type in _TEXT_TYPES:
        text = msg.get("text") or msg.get("content", {}).get("text", "")
        if not text or not text.strip():
            return None

        # For replies, prepend the quoted message so the LLM has context
        if msg_type == "ExtendedTextMessage":
            quoted = msg.get("quoted", "")
            if quoted and isinstance(quoted, str):
                text = f'[Em resposta a: "{quoted}"]\n{text}'
            elif quoted and isinstance(quoted, dict):
                quoted_text = quoted.get("text") or quoted.get("body", "")
                if quoted_text:
                    text = f'[Em resposta a: "{quoted_text}"]\n{text}'

        return phone, text.strip()

    if msg_type in _MEDIA_TYPES:
        message_id = msg.get("messageid") or msg.get("id", "")
        if not message_id:
            return None
        text = await process_media(message_id, msg_type, phone=phone)
        if not text:
            return None
        return phone, text

    return None


_PAUSE_CMD   = "~Obrigada, Eva!~"
_RESUME_CMD  = "~Eva, consegue dar continuidade no atendimento?~"
_HOLD_HOURS  = 24


async def _pause_bot_for_patient(phone: str) -> None:
    """Desativa o bot para o paciente pelo período de hold (24 h)."""
    from app.database import upsert_user
    await upsert_user(phone, {
        "active": False,
        "deactivated_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info("Bot pausado para %s pelo comando da atendente", phone)


async def _resume_bot_for_patient(phone: str) -> None:
    """Reativa o bot para o paciente imediatamente."""
    from app.database import upsert_user
    await upsert_user(phone, {
        "active": True,
        "deactivated_at": None,
    })
    logger.info("Bot reativado para %s pelo comando da atendente", phone)
    # O bot vai responder na próxima mensagem do paciente — sem enviar nada agora.


async def process_message(phone: str, text: str) -> None:
    """Route a (possibly debounced) message through the LangGraph chatbot."""
    config = {"configurable": {"thread_id": phone, "phone": phone}}

    # If user was transferred to human or paused by attendant, bot stays silent.
    # After 24 h, auto-reactivate when patient sends a new message.
    existing = await get_user_by_phone(phone)
    if existing and existing.get("active") is False:
        deactivated_at = existing.get("deactivated_at")
        if deactivated_at:
            dt = datetime.fromisoformat(deactivated_at)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - dt < timedelta(hours=_HOLD_HOURS):
                return  # still within the 24-h hold
            # 24 h elapsed — reactivate automatically and proceed
            from app.database import upsert_user
            await upsert_user(phone, {"active": True, "deactivated_at": None})
            logger.info("Bot auto-reativado para %s após 24 h", phone)
        else:
            return  # no timestamp = permanent hold, never auto-reactivate

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

    # Apaga mensagens, usuário e estado do checkpointer
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
        msg = payload.get("message", {})
        msg_type = msg.get("messageType", "unknown")
        logger.info("Incoming messageType=%s fromMe=%s", msg_type, msg.get("fromMe"))

        # Detect attendant commands typed directly in WhatsApp (fromMe but not via API)
        if msg.get("fromMe") and not msg.get("wasSentByApi") and not msg.get("isGroup"):
            raw_text = (msg.get("text") or msg.get("content", {}).get("text") or "").strip()
            phone = msg.get("chatid", "")
            if phone:
                if raw_text == _PAUSE_CMD:
                    await _pause_bot_for_patient(phone)
                    return
                if raw_text == _RESUME_CMD:
                    await _resume_bot_for_patient(phone)
                    return

        # Check /reset directly from raw payload (before type filtering)
        if not msg.get("fromMe") and not msg.get("isGroup"):
            raw_text = (msg.get("text") or msg.get("content", {}).get("text") or "").strip().lower()
            if raw_text == "/reset":
                phone = msg.get("chatid", "")
                if phone:
                    await _reset_conversation(phone)
                    return

        result = await extract_message(payload)
        if result is None:
            logger.info("Message ignored (type=%s)", msg_type)
            return
        phone, text = result


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


@app.get("/health")
async def health():
    return {"status": "ok"}
