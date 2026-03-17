"""Tests for process_message() — conversation routing logic."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from langchain_core.messages import HumanMessage

from tests.conftest import PHONE, CONFIG

# A user record that has all required fields filled in
_KNOWN_USER = {
    "id": "user-uuid-123",
    "number": "5583999999999",
    "name": "Maria",
    "patient_name": "Maria",
    "age": 30,
    "is_patient": True,
    "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",  # julio
    "active": True,
}


def _make_chatbot(snapshot_values=None):
    chatbot = MagicMock()
    chatbot.aget_state = AsyncMock(
        return_value=MagicMock(values=snapshot_values or {})
    )
    chatbot.ainvoke = AsyncMock(return_value={})
    return chatbot


# ── helpers ───────────────────────────────────────────────────────────────────

def _patch_deps(get_user_return=None, snapshot_values=None):
    """Return a context-manager stack that patches all external dependencies."""
    chatbot = _make_chatbot(snapshot_values)
    return (
        patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=get_user_return),
        patch("app.main.log_event", new_callable=AsyncMock),
        patch("app.graph.graph.chatbot", chatbot),
        chatbot,
    )


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_new_user_initializes_collect_info():
    with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.main.log_event", new_callable=AsyncMock), \
         patch("app.graph.graph.chatbot") as mock_chatbot_attr:
        chatbot = _make_chatbot()
        mock_chatbot_attr.__get__ = lambda *_: chatbot
        # Directly replace the module attribute
        import app.graph.graph as gg
        original = gg.chatbot
        gg.chatbot = chatbot
        try:
            from app.main import process_message
            await process_message(PHONE, "oi")
            state_update = chatbot.ainvoke.call_args[0][0]
            assert state_update["stage"] == "collect_info"
            assert state_update["phone"] == PHONE
        finally:
            gg.chatbot = original


async def test_known_user_goes_to_patient_agent():
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=_KNOWN_USER), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "quero remarcar")
            state_update = chatbot.ainvoke.call_args[0][0]
            assert state_update["stage"] == "patient_agent"
            assert state_update["preferred_doctor"] == "julio"
    finally:
        gg.chatbot = original


async def test_inactive_user_returns_silently():
    inactive_user = {**_KNOWN_USER, "active": False}
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=inactive_user):
            from app.main import process_message
            await process_message(PHONE, "oi")
            chatbot.ainvoke.assert_not_called()
    finally:
        gg.chatbot = original


async def test_existing_snapshot_adds_only_human_message():
    """When the graph already has state, only inject the new HumanMessage."""
    import app.graph.graph as gg
    existing_state = {"stage": "patient_agent", "messages": [HumanMessage(content="anterior")]}
    chatbot = _make_chatbot(snapshot_values=existing_state)
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=_KNOWN_USER), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "nova mensagem")
            state_update = chatbot.ainvoke.call_args[0][0]
            # Only messages key — no stage re-initialization
            assert list(state_update.keys()) == ["messages"]
            assert state_update["messages"][0].content == "nova mensagem"
    finally:
        gg.chatbot = original


async def test_log_event_called_for_new_conversation():
    """log_event('conversation_started') must fire when snapshot is empty."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
            from app.main import process_message
            await process_message(PHONE, "oi")
            mock_log.assert_awaited_once_with("conversation_started", PHONE)
    finally:
        gg.chatbot = original


async def test_langfuse_callbacks_injected_when_handler_set():
    """If _langfuse_handler is configured, it appears in the invocation config."""
    import app.graph.graph as gg
    import app.main as main_module
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    fake_handler = object()
    original_handler = main_module._langfuse_handler
    main_module._langfuse_handler = fake_handler
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi")
            _, kwargs = chatbot.ainvoke.call_args
            config = kwargs.get("config") or chatbot.ainvoke.call_args[0][1]
            assert fake_handler in config.get("callbacks", [])
    finally:
        gg.chatbot = original
        main_module._langfuse_handler = original_handler
