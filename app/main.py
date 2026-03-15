import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from langchain_core.messages import HumanMessage

from app.graph import graph as graph_module
from app.database import get_user_by_phone, log_event, DOCTOR_NAMES
from app.buffer import push as buffer_push
from app.auth import router as auth_router

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
        text = await process_media(message_id, msg_type)
        if not text:
            return None
        return phone, text

    return None


async def process_message(phone: str, text: str) -> None:
    """Route a (possibly debounced) message through the LangGraph chatbot."""
    config = {"configurable": {"thread_id": phone, "phone": phone}}

    snapshot = await graph_module.chatbot.aget_state(config)
    if snapshot.values:
        state_update = {"messages": [HumanMessage(content=text)]}
    else:
        await log_event("conversation_started", phone)
        existing = await get_user_by_phone(phone)
        _REQUIRED = ("name", "patient_name", "age", "is_patient", "doctor_id")
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
            "is_patient": existing.get("is_patient") if existing else None,
            "preferred_doctor": doctor_key,
        }

    await graph_module.chatbot.ainvoke(state_update, config=config)


async def _handle_payload(payload: dict) -> None:
    result = await extract_message(payload)
    if result is None:
        return
    phone, text = result
    await buffer_push(phone, text, process_message)


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    logger.debug("Webhook payload: %s", payload)
    asyncio.create_task(_handle_payload(payload))
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
