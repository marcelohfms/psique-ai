import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")


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

    if msg_type == "document":
        mime = msg.get("document", {}).get("mime_type", "").lower()
        if "pdf" in mime:
            media_id = msg.get("document", {}).get("id", "")
            if media_id:
                try:
                    from app.whatsapp import download_media
                    from app.media import describe_pdf_bytes
                    pdf_bytes = await download_media(media_id)
                    text = await describe_pdf_bytes(pdf_bytes, phone)
                    if not text:
                        # Medical document — already handled (thank-you sent, clinic notified)
                        return None
                    return phone, text
                except Exception:
                    logger.exception("Failed to process PDF media_id=%s", media_id)
            return None
        return None  # other document types: ignore

    # reaction, sticker, location, etc. — ignore
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


@app.post("/admin/patch-state")
async def admin_patch_state(request: Request, x_admin_secret: str | None = Header(default=None)):
    """Patch the live LangGraph state for a patient mid-conversation.

    Useful when a patient already sent correct info but the bot didn't capture it.
    Accepted fields: birth_date (dd/mm/yyyy), patient_age, patient_name, preferred_doctor,
    is_patient, patient_email, stage.

    Body: {"phone": "5583...", "birth_date": "15/01/1990"}
    """
    _check_admin_secret(x_admin_secret)
    body = await request.json()
    phone = body.get("phone", "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    if not phone.endswith("@s.whatsapp.net"):
        phone += "@s.whatsapp.net"

    _PATCHABLE = {
        "birth_date", "patient_age", "patient_name", "user_name",
        "preferred_doctor", "is_patient", "patient_email", "stage",
    }
    patch: dict = {k: v for k, v in body.items() if k in _PATCHABLE}
    if not patch:
        raise HTTPException(status_code=400, detail=f"No patchable fields. Accepted: {sorted(_PATCHABLE)}")

    # Auto-calculate age from birth_date if not provided
    if "birth_date" in patch and "patient_age" not in patch:
        from app.graph.schemas import _parse_birth_date
        from datetime import datetime as _dt
        normalised = _parse_birth_date(patch["birth_date"])
        if not normalised:
            raise HTTPException(status_code=400, detail="Invalid birth_date format. Use dd/mm/yyyy.")
        patch["birth_date"] = normalised
        bd = _dt.strptime(normalised, "%d/%m/%Y")
        today = _dt.now()
        patch["patient_age"] = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))

    config = {"configurable": {"thread_id": phone, "phone": phone}}

    # Update LangGraph checkpoint
    await graph_module.chatbot.aupdate_state(config, patch)

    # Mirror to Supabase
    db_patch: dict = {}
    if "birth_date" in patch:
        db_patch["birth_date"] = patch["birth_date"]
    if "patient_age" in patch:
        db_patch["age"] = patch["patient_age"]
    if "patient_name" in patch:
        db_patch["patient_name"] = patch["patient_name"]
    if "user_name" in patch:
        db_patch["name"] = patch["user_name"]
    if "preferred_doctor" in patch:
        db_patch["doctor_id"] = DOCTOR_NAMES.get(patch["preferred_doctor"])
    if "patient_email" in patch:
        db_patch["email"] = patch["patient_email"]
    if db_patch:
        from app.database import upsert_user
        await upsert_user(phone, db_patch)

    logger.info("PATCH_STATE phone=%s patch=%s", phone, patch)

    # Trigger Eva to continue immediately without waiting for the patient
    triggered = False
    trigger_error = None
    try:
        from langchain_core.messages import HumanMessage as _HM
        await graph_module.chatbot.ainvoke(
            {"messages": [_HM(content="[sistema-interno]: retomar")], "phone": phone},
            config,
        )
        triggered = True
    except Exception as exc:
        trigger_error = str(exc)
        logger.warning("PATCH_STATE trigger failed phone=%s error=%s", phone, exc)

    result: dict = {"status": "patched", "phone": phone, "applied": patch, "triggered": triggered}
    if trigger_error:
        result["trigger_error"] = trigger_error
    return result


# ── Core message processing ───────────────────────────────────────────────────

async def process_message(phone: str, text: str) -> None:
    """Route a (possibly debounced) message through the LangGraph chatbot."""
    config = {"configurable": {"thread_id": phone, "phone": phone}, "recursion_limit": 15}

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
        state_update = {"messages": [HumanMessage(content=text)], "silent_mode": False, "phone": phone}
        # If the conversation is still in collect_info, re-sync any fields that
        # were filled in the DB since the conversation started (e.g. by an attendant).
        # If the patient is already fully registered, skip collect_info entirely.
        if snapshot.values.get("stage") == "collect_info" and existing:
            if existing.get("name") and existing.get("is_patient") is not None:
                # Patient is registered — no need to go through collect_info again
                state_update["stage"] = "patient_agent"
            db_sync: dict = {}
            _syncable = {
                "name":       "user_name",
                "patient_name": "patient_name",
                "age":        "patient_age",
                "birth_date": "birth_date",
                "email":      "patient_email",
                "doctor_id":  None,   # handled separately below
                "is_patient": "is_patient",
                "guardian_name": "guardian_name",
                "guardian_cpf":  "guardian_cpf",
                "guardian_relationship": "guardian_relationship",
                "patient_cpf": "patient_cpf",
            }
            for db_field, state_field in _syncable.items():
                if state_field is None:
                    continue
                db_val = existing.get(db_field)
                if db_val is not None and snapshot.values.get(state_field) is None:
                    db_sync[state_field] = db_val
            # doctor_id → preferred_doctor key
            if existing.get("doctor_id") and snapshot.values.get("preferred_doctor") is None:
                db_sync["preferred_doctor"] = DOCTOR_NAMES.get(existing["doctor_id"])
            if db_sync:
                state_update.update(db_sync)
    else:
        await log_event("conversation_started", phone)
        # Only name + is_patient are required to route to patient_agent.
        # Missing birth_date, email, CPF etc. are collected in context when actually needed,
        # not upfront — avoids interrogating registered patients at every conversation start.
        _REQUIRED = ("name", "is_patient")
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

def _extract_chatwoot_message(payload: dict) -> tuple[str, str | None, int] | None:
    """
    Extract (phone, text_or_None, conversation_id) from a Chatwoot Agent Bot webhook.
    Returns None for outgoing/activity messages or missing phone/conversation.
    text is None when the message has no body but may have attachments.
    message_type: 0=incoming, 1=outgoing, 2=activity
    """
    if payload.get("message_type") not in (0, "incoming"):
        return None
    if payload.get("sender", {}).get("type") in ("agent_bot", "bot"):
        return None
    content = (payload.get("content") or "").strip() or None
    attachments = payload.get("attachments", [])
    if not content and not attachments:
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


async def _process_chatwoot_attachments(attachments: list) -> str | None:
    """Download and process the first recognisable attachment (audio or image)."""
    import httpx
    from app.media import transcribe_audio_bytes, describe_image_bytes, describe_pdf_bytes

    for att in attachments:
        file_type = (att.get("file_type") or "").lower()
        data_url = att.get("data_url") or att.get("thumb_url") or ""
        if not data_url:
            continue
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(data_url, follow_redirects=True)
                resp.raise_for_status()
                media_bytes = resp.content
            if file_type == "audio":
                return await transcribe_audio_bytes(media_bytes)
            if file_type == "image":
                return await describe_image_bytes(media_bytes)
            if file_type == "file":
                content_type = (att.get("content_type") or "").lower()
                if "pdf" in content_type or data_url.lower().endswith(".pdf"):
                    return await describe_pdf_bytes(media_bytes)
                return "[pdf-recebido]"
        except Exception:
            logger.exception("Failed to process Chatwoot attachment type=%s url=%.80s", file_type, data_url)
    return None


_EVA_INACTIVE_LABEL = "eva-inativa"
_EVA_ACTIVE_LABEL = "eva-ativa"

# In-memory label tracker: conv_id (str) → frozenset of current labels
# Used to detect label changes from message_updated events (Chatwoot doesn't send a dedicated label-change event)
_conv_labels: dict[str, frozenset] = {}


def _extract_phone_from_payload(payload: dict) -> str | None:
    """Extract the patient phone from any Chatwoot payload."""
    conversation = payload.get("conversation", {})
    phone_raw = (
        conversation.get("meta", {}).get("sender", {}).get("phone_number")
        or payload.get("meta", {}).get("sender", {}).get("phone_number")
        or payload.get("sender", {}).get("phone_number")
        # fallback: contact identifier (Evolution stores the raw number here)
        or conversation.get("meta", {}).get("sender", {}).get("identifier")
        or payload.get("contact", {}).get("phone_number")
        or ""
    ).strip()
    logger.info("EXTRACT_PHONE raw=%r conversation_meta=%s",
                phone_raw,
                conversation.get("meta", {}).get("sender", {}))
    if not phone_raw:
        return None
    return phone_raw.lstrip("+") + "@s.whatsapp.net"


async def _apply_eva_label_action(payload: dict, added: set, removed: set) -> bool:
    """Pause/resume Eva based on which control labels were added or removed."""
    phone = _extract_phone_from_payload(payload)
    logger.info("EVA_LABEL_ACTION phone=%s added=%s removed=%s", phone, added, removed)
    if not phone:
        logger.warning("EVA_LABEL_ACTION: could not extract phone from payload keys=%s", list(payload.keys()))
        return True

    if _EVA_INACTIVE_LABEL in added:
        await _pause_bot_for_patient(phone)
        logger.info("Eva pausada via label para %s", phone)

    elif _EVA_INACTIVE_LABEL in removed:
        await _resume_bot_for_patient(phone)
        logger.info("Eva reativada via remoção de eva-inativa para %s", phone)

    elif _EVA_ACTIVE_LABEL in added:
        await _resume_bot_for_patient(phone)
        logger.info("Eva reativada via label eva-ativa para %s", phone)

        conversation_id = payload.get("conversation", {}).get("id")
        if conversation_id:
            try:
                from app.chatwoot import get_last_patient_message
                last_msg = await get_last_patient_message(conversation_id)
                if last_msg:
                    await buffer_push(phone, last_msg, process_message)
            except Exception:
                logger.exception("Failed to fetch/reprocess last message for %s", phone)

    return True


async def _handle_label_change(payload: dict) -> bool:
    """
    Handle label changes from Chatwoot that control Eva's pause/resume state.
    Chatwoot may fire conversation_updated or message_updated depending on configuration.
    Returns True if the event was a label change we handled, False otherwise.
    """
    event = payload.get("event")

    # Path 1: conversation_updated with changed_attributes (standard Chatwoot account webhook)
    if event == "conversation_updated":
        logger.info("CHATWOOT_CONV_UPDATED payload_keys=%s labels=%s conv_labels=%s changed_attrs=%s",
                    list(payload.keys()),
                    payload.get("labels"),
                    payload.get("conversation", {}).get("labels"),
                    payload.get("changed_attributes"))
        changed = payload.get("changed_attributes") or []
        logger.info("CONV_UPDATED_CHANGED_ATTRS type=%s value=%s", type(changed).__name__, changed)
        label_change = next(
            (c for c in changed if isinstance(c, dict) and "labels" in c),
            None,
        )
        if label_change is None:
            # Fallback: if eva-ativa is in current labels and not tracked yet, treat as added
            conv_id = str(payload.get("conversation", {}).get("id") or "")
            labels_now = frozenset(
                payload.get("conversation", {}).get("labels")
                or payload.get("labels")
                or []
            )
            if conv_id:
                previous = _conv_labels.get(conv_id, frozenset())
                _conv_labels[conv_id] = labels_now
                added = labels_now - previous
                removed = previous - labels_now
                logger.info("CONV_UPDATED_FALLBACK conv=%s added=%s removed=%s", conv_id, added, removed)
                if _EVA_ACTIVE_LABEL in added or _EVA_INACTIVE_LABEL in added or _EVA_INACTIVE_LABEL in removed:
                    return await _apply_eva_label_action(payload, added, removed)
            return False

        # Labels may be at payload["conversation"]["labels"] (agent-bot format)
        # or at payload["labels"] (account webhook format) — check both.
        labels_now = set(
            payload.get("conversation", {}).get("labels")
            or payload.get("labels")
            or []
        )
        labels_before = set((label_change.get("labels") or {}).get("previous_value") or [])
        added = labels_now - labels_before
        removed = labels_before - labels_now

        logger.info(
            "LABEL_CHANGE conversation_updated labels_now=%s labels_before=%s added=%s removed=%s",
            labels_now, labels_before, added, removed,
        )

        if _EVA_INACTIVE_LABEL not in added and _EVA_INACTIVE_LABEL not in removed and _EVA_ACTIVE_LABEL not in added:
            return False

        return await _apply_eva_label_action(payload, added, removed)

    # Path 2: message_updated / conversation_resolved — Chatwoot fires these when labels change.
    # We track labels per conversation in memory to detect actual changes.
    if event in ("message_updated", "conversation_resolved"):
        conv = payload.get("conversation") or {}
        conv_id = str(conv.get("id") or payload.get("id") or "")
        # Labels live inside conversation{} for message_updated, at top level for conversation_resolved
        labels_now = frozenset(conv.get("labels") or payload.get("labels") or [])

        previous = _conv_labels.get(conv_id, frozenset())
        _conv_labels[conv_id] = labels_now

        added = labels_now - previous
        removed = previous - labels_now

        logger.info(
            "LABEL_CHANGE %s conv=%s labels_now=%s previous=%s added=%s removed=%s",
            event, conv_id, labels_now, previous, added, removed,
        )

        # eva-ativa takes priority: resume always beats pause
        if _EVA_ACTIVE_LABEL in added:
            return await _apply_eva_label_action(payload, added={_EVA_ACTIVE_LABEL}, removed=set())
        if _EVA_INACTIVE_LABEL in added:
            return await _apply_eva_label_action(payload, added={_EVA_INACTIVE_LABEL}, removed=set())
        if _EVA_INACTIVE_LABEL in removed:
            return await _apply_eva_label_action(payload, added=set(), removed={_EVA_INACTIVE_LABEL})

        return False

    return False


async def _handle_attendant_note(payload: dict) -> None:
    """
    Process a private note written by a human agent when eva-ativa label is present.
    The note is injected into Eva's pipeline as an internal instruction so she can
    continue the conversation without transferring to a human.
    """
    sender = payload.get("sender", {})
    # Ignore private notes sent by the bot itself (agent_bot type)
    if sender.get("type") in ("agent_bot", "bot"):
        return

    phone = _extract_phone_from_payload(payload)
    if not phone:
        return

    content = (payload.get("content") or "").strip()
    if not content:
        return

    conv_id = payload.get("conversation", {}).get("id")
    if conv_id:
        from app.chatwoot import register_conversation
        register_conversation(phone, conv_id)

    logger.info("ATTENDANT_NOTE phone=%s conv=%s content=%.120s", phone, conv_id, content)

    async def _run_silent(p: str, text: str) -> None:
        config = {"configurable": {"thread_id": p, "phone": p}}
        state_update = {"messages": [HumanMessage(content=text)], "silent_mode": True, "phone": p}
        await graph_module.chatbot.ainvoke(state_update, config=config)

    instruction = f"[Instrução da atendente]: {content}"
    await buffer_push(phone, instruction, _run_silent)


async def _handle_chatwoot_payload(payload: dict) -> None:
    try:
        # ── Private note from human agent → Eva instruction ───────────────────
        logger.info(
            "CHATWOOT_PAYLOAD event=%s private=%s message_type=%s sender_type=%s labels=%s",
            payload.get("event"),
            payload.get("private"),
            payload.get("message_type"),
            payload.get("sender", {}).get("type"),
            payload.get("conversation", {}).get("labels"),
        )
        if payload.get("message_type") in (1, "outgoing"):
            if payload.get("private"):
                await _handle_attendant_note(payload)
            return

        # ── Label change: eva-inativa added/removed ───────────────────────────
        if await _handle_label_change(payload):
            return

        # ── Incoming patient message ──────────────────────────────────────────
        result = _extract_chatwoot_message(payload)
        if result is None:
            return
        phone, text, conversation_id = result

        from app.chatwoot import register_conversation, reopen_conversation
        register_conversation(phone, conversation_id)

        # Reopen pending conversations (patient replied to a resolved conversation)
        if payload.get("conversation", {}).get("status") == "pending":
            try:
                await reopen_conversation(conversation_id)
                logger.info("Reopened pending conversation %s for %s", conversation_id, phone)
            except Exception:
                logger.warning("Failed to reopen conversation %s", conversation_id)

        # If eva-ativa label is present on the conversation, force-reactivate Eva now.
        # This handles cases where the label was added but the activation event was missed
        # (server restart, race condition, etc.).
        conv_labels = set(payload.get("conversation", {}).get("labels") or [])
        if _EVA_ACTIVE_LABEL in conv_labels:
            await _resume_bot_for_patient(phone)
        elif _EVA_INACTIVE_LABEL in conv_labels:
            return

        if text is None:
            text = await _process_chatwoot_attachments(payload.get("attachments", []))
            if not text:
                return

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
    asyncio.create_task(_handle_chatwoot_payload(payload))
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Admin: trigger appointment reminders ──────────────────────────────────────

@app.post("/admin/send-reminders")
async def admin_send_reminders(x_admin_secret: str | None = Header(default=None)):
    """
    Trigger appointment reminder messages (lembrete_dia_anterior / lembrete_dia_consulta).
    Protected by X-Admin-Secret header. Intended to be called by a daily cron job.
    """
    _check_admin_secret(x_admin_secret)
    from scripts.send_appointment_reminders import main as run_reminders
    try:
        await run_reminders()
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("send-reminders failed")
        raise HTTPException(status_code=500, detail=str(exc))
