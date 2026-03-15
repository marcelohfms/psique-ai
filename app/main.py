import logging
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from langchain_core.messages import HumanMessage

from app.graph import graph as graph_module
from app.database import get_user_by_phone, DOCTOR_NAMES
from app.buffer import push as buffer_push
from app.auth import router as auth_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Supabase checkpointer on startup if connection string is set."""
    conn_string = os.getenv("SUPABASE_CONNECTION_STRING")

    if conn_string:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph

        logger.info("Connecting to Supabase checkpointer...")
        async with AsyncPostgresSaver.from_conn_string(conn_string, pipeline=False) as checkpointer:
            await checkpointer.setup()
            graph_module.chatbot = build_graph(checkpointer=checkpointer)
            logger.info("Supabase checkpointer ready.")
            yield
    else:
        logger.warning("SUPABASE_CONNECTION_STRING not set — using in-memory checkpointer.")
        yield


app = FastAPI(title="Psique Chatbot", lifespan=lifespan)
app.include_router(auth_router)


def extract_message(payload: dict) -> tuple[str, str] | None:
    """
    Extract (phone, text) from a UAZAPI webhook payload.

    Expected structure:
    {
      "message": {
        "fromMe": false,
        "isGroup": false,
        "wasSentByApi": false,
        "type": "text",
        "chatid": "5511999999999@s.whatsapp.net",
        "text": "Olá"
      }
    }
    """
    msg = payload.get("message", {})

    if msg.get("fromMe") or msg.get("wasSentByApi"):
        return None
    if msg.get("isGroup"):
        return None
    if msg.get("type") != "text":
        return None

    phone = msg.get("chatid", "")
    if not phone:
        return None

    text = msg.get("text") or msg.get("content", {}).get("text", "")
    if not text or not text.strip():
        return None

    return phone, text.strip()


async def process_message(phone: str, text: str) -> None:
    """Route a (possibly debounced) message through the LangGraph chatbot."""
    config = {"configurable": {"thread_id": phone, "phone": phone}}

    snapshot = graph_module.chatbot.get_state(config)
    if snapshot.values:
        state_update = {"messages": [HumanMessage(content=text)]}
    else:
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


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    logger.debug("Webhook payload: %s", payload)

    result = extract_message(payload)
    if result is None:
        return {"status": "ignored"}

    phone, text = result

    # Buffer the message — handler fires after DEBOUNCE_SECONDS with no new messages
    await buffer_push(phone, text, process_message)

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
