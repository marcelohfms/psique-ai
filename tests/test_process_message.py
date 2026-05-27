"""Tests for process_message() — conversation routing logic."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from langchain_core.messages import HumanMessage, AIMessage

from tests.conftest import PHONE, CONFIG


# ── CollectInfoOutput schema validation ───────────────────────────────────────

def test_collect_info_output_accepts_valid_birth_date():
    from app.graph.schemas import CollectInfoOutput
    obj = CollectInfoOutput(reply="ok", birth_date="15/01/1994")
    assert obj.birth_date == "15/01/1994"


def test_collect_info_output_normalises_iso_birth_date():
    """LLM may return ISO format — we normalise to dd/mm/yyyy, not reject."""
    from app.graph.schemas import CollectInfoOutput
    obj = CollectInfoOutput(reply="ok", birth_date="1994-01-15")
    assert obj.birth_date == "15/01/1994"


def test_collect_info_output_normalises_dot_separated_date():
    from app.graph.schemas import CollectInfoOutput
    obj = CollectInfoOutput(reply="ok", birth_date="15.01.1994")
    assert obj.birth_date == "15/01/1994"


def test_collect_info_output_rejects_unparseable_date():
    from app.graph.schemas import CollectInfoOutput
    obj = CollectInfoOutput(reply="ok", birth_date="not-a-date")
    assert obj.birth_date is None


@pytest.mark.parametrize("raw,expected", [
    ("15/01/85",   "15/01/1985"),   # 2-digit year slash
    ("15-01-85",   "15/01/1985"),   # 2-digit year dash
    ("15.01.85",   "15/01/1985"),   # 2-digit year dot
    ("15 01 1985", "15/01/1985"),   # space separator
    ("15 01 85",   "15/01/1985"),   # space + 2-digit year
    ("15011985",   "15/01/1985"),   # no separator 8 digits
    ("150185",     "15/01/1985"),   # no separator 6 digits
    ("05/06/2010", "05/06/2010"),   # normal format still works
])
def test_parse_birth_date_flexible_formats(raw, expected):
    from app.graph.schemas import _parse_birth_date
    assert _parse_birth_date(raw) == expected


async def test_collect_info_node_overrides_reply_on_invalid_birth_date():
    """When the LLM extracts an invalid birth_date, the node sends a correction message."""
    from app.graph.nodes import collect_info_node
    from app.graph.schemas import CollectInfoOutput

    invalid_result = CollectInfoOutput(
        reply="Anotei sua data de nascimento.",
        birth_date="not-a-date",  # genuinely unparseable — validator returns None
        is_complete=False,
    )
    # birth_date is in model_fields_set even though validator set it to None
    assert "birth_date" in invalid_result.model_fields_set
    assert invalid_result.birth_date is None

    state = {
        "phone": PHONE,
        "messages": [HumanMessage(content="nasci em 1994-01-15")],
        "birth_date": None,
    }
    with patch("app.graph.nodes._get_collect_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=invalid_result)
        mock_llm_fn.return_value = mock_llm
        await collect_info_node(state, {})

    sent_text = mock_send.call_args[0][1]
    assert "dd/mm/aaaa" in sent_text
    assert "Anotei" not in sent_text  # original reply must not be sent

# A user record with only the minimum required fields (name + is_patient).
# Intentionally missing birth_date, email, age — they should NOT trigger collect_info.
_KNOWN_USER = {
    "id": "user-uuid-123",
    "number": "5583999999999",
    "name": "Maria",
    "patient_name": "Maria",
    "age": None,
    "is_patient": True,
    "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",  # julio
    "birth_date": None,
    "email": None,
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
         patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
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
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[_KNOWN_USER]), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "quero remarcar")
            state_update = chatbot.ainvoke.call_args[0][0]
            assert state_update["stage"] == "patient_agent"
            assert state_update["preferred_doctor"] == "julio"
    finally:
        gg.chatbot = original


async def test_known_user_missing_optional_fields_goes_to_patient_agent():
    """A registered patient missing birth_date/email/age must go to patient_agent, not collect_info."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    incomplete_user = {
        **_KNOWN_USER,
        "birth_date": None,
        "email": None,
        "age": None,
    }
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=incomplete_user), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[incomplete_user]), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi")
            state_update = chatbot.ainvoke.call_args[0][0]
            assert state_update["stage"] == "patient_agent"
    finally:
        gg.chatbot = original


async def test_inactive_user_returns_silently():
    inactive_user = {**_KNOWN_USER, "active": False}
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=inactive_user), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[inactive_user]):
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
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[_KNOWN_USER]), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "nova mensagem")
            state_update = chatbot.ainvoke.call_args[0][0]
            # messages + silent_mode reset + phone — no stage re-initialization
            assert set(state_update.keys()) == {"messages", "silent_mode", "phone"}
            assert state_update["silent_mode"] is False
            assert state_update["messages"][0].content == "nova mensagem"
    finally:
        gg.chatbot = original


# ── Guardian info collection for minors ──────────────────────────────────────

def _base_minor_state(**kwargs) -> dict:
    """Minimal collect_info state for a minor patient."""
    base = {
        "phone": PHONE,
        "stage": "collect_info",
        "user_name": "Ana",
        "patient_name": "Ana",
        "patient_age": None,
        "birth_date": None,
        "patient_cpf": "111.222.333-00",
        "guardian_name": None,
        "guardian_cpf": None,
        "guardian_relationship": None,
        "is_for_self": None,
        "is_patient": None,
        "preferred_doctor": None,
        "patient_email": None,
        "consultation_reason": None,
        "referral_professional": None,
        "medication_note": None,
        "pending_patients": None,
        "user_db_id": None,
        "silent_mode": None,
        "messages": [],
    }
    base.update(kwargs)
    return base


async def test_collect_info_asks_guardian_name_after_minor_birth_date():
    """After birth_date reveals patient < 18, the next question must be guardian name."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage
    from datetime import date

    birth = date(2015, 3, 15)
    today = date.today()
    expected_age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

    state = _base_minor_state(
        user_name="Ana",
        patient_name="Ana",
        patient_cpf="111.222.333-00",
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual a data de nascimento do paciente? (formato dd/mm/aaaa)"),
            HumanMessage(content="15/03/2015"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("birth_date") == "15/03/2015"
    assert result.get("patient_age") == expected_age
    assert expected_age < 18, "Test pre-condition: patient must be a minor"
    sent = mock_send.call_args[0][1]
    assert "responsável" in sent.lower()


async def test_collect_info_asks_guardian_cpf_after_guardian_name():
    """After guardian_name is collected for a minor, the next step must be guardian CPF."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        patient_age=10,
        birth_date="15/03/2015",
        messages=[
            # request keyword so _has_request=True; AIMessage so _has_greeted=True
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual é o nome completo do responsável pelo paciente?"),
            HumanMessage(content="Maria Souza"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("guardian_name") == "Maria Souza"
    sent = mock_send.call_args[0][1]
    assert "cpf" in sent.lower()


async def test_collect_info_proceeds_to_is_patient_after_guardian_cpf():
    """After guardian_cpf is collected for a minor, the next step must be is_patient."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        patient_age=10,
        birth_date="15/03/2015",
        guardian_name="Maria Souza",
        messages=[
            # request keyword so _has_request=True; AIMessage so _has_greeted=True
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual é o CPF do responsável?"),
            HumanMessage(content="123.456.789-00"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("guardian_cpf") == "123.456.789-00"
    sent = mock_send.call_args[0][1]
    # After guardian_cpf, next question is is_patient
    assert "paciente" in sent.lower() or "clínica" in sent.lower()


async def test_collect_info_no_to_is_patient_sets_returning_patient_false():
    """'não' to 'já é paciente da clínica?' must set is_returning_patient=False, NOT is_patient=False."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "O paciente já é paciente da clínica?"
    state = _base_minor_state(
        patient_age=30,
        birth_date="15/03/1994",
        patient_cpf="123.456.789-00",
        is_for_self=True,  # already answered — prevents Step 4d from intercepting
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content=_Q),
            HumanMessage(content="não"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is False, "is_returning_patient must be False for new patient"
    assert result.get("is_patient") is None, "is_patient must NOT be set by the 'já é paciente?' question"


async def test_collect_info_yes_to_is_patient_sets_returning_patient_true():
    """'sim' to 'já é paciente da clínica?' must set is_returning_patient=True, NOT touch is_patient."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "O paciente já é paciente da clínica?"
    state = _base_minor_state(
        patient_age=30,
        birth_date="15/03/1994",
        patient_cpf="123.456.789-00",
        is_for_self=True,  # already answered — prevents Step 4d from intercepting
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content=_Q),
            HumanMessage(content="sim"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is True, "is_returning_patient must be True for returning patient"
    assert result.get("is_patient") is None, "is_patient must NOT be set by the 'já é paciente?' question"


async def test_collect_info_adult_birth_date_asks_is_for_self():
    """After birth date for an adult, the next question must ask if the contact is the patient."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="João Silva",
        patient_name="João Silva",
        patient_cpf="123.456.789-00",
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual a data de nascimento do paciente? (formato dd/mm/aaaa)"),
            HumanMessage(content="15/03/1990"),  # adult
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    sent = mock_send.call_args[0][1]
    assert "agendando em nome" in sent.lower() or "você é" in sent.lower()
    assert result.get("is_for_self") is None  # not yet answered


async def test_collect_info_is_for_self_yes_proceeds_to_clinic_question():
    """'sou eu' to the is_for_self question must set is_for_self=True and proceed."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="João Silva",
        patient_name="João Silva",
        patient_cpf="123.456.789-00",
        patient_age=34,
        birth_date="15/03/1990",
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Você é o(a) paciente João ou está agendando em nome dele(a)?"),
            HumanMessage(content="sou eu mesmo"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_for_self") is True
    sent = mock_send.call_args[0][1]
    assert "paciente da clínica" in sent.lower() or "paciente" in sent.lower()


async def test_collect_info_is_for_self_no_asks_contact_name():
    """'sou a mãe' to the is_for_self question must set is_for_self=False and ask contact name."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="João Silva",
        patient_name="João Silva",
        patient_cpf="123.456.789-00",
        patient_age=34,
        birth_date="15/03/1990",
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Você é o(a) paciente João ou está agendando em nome dele(a)?"),
            HumanMessage(content="não, sou a mãe"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_for_self") is False
    sent = mock_send.call_args[0][1]
    assert "nome completo" in sent.lower() and "contato" in sent.lower()


async def test_collect_info_contact_name_updates_user_name():
    """Contact name answer must update user_name (not patient_name) and proceed to clinic question."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _CONTACT_Q = "Qual o seu nome completo para contato?"
    state = _base_minor_state(
        user_name="João Silva",
        patient_name="João Silva",
        patient_cpf="123.456.789-00",
        patient_age=34,
        birth_date="15/03/1990",
        is_for_self=False,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content=_CONTACT_Q),
            HumanMessage(content="Maria Silva"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("user_name") == "Maria Silva"
    assert result.get("patient_name") is None  # patient_name must NOT be changed
    sent = mock_send.call_args[0][1]
    assert "paciente da clínica" in sent.lower()


async def test_collect_info_guardian_name_also_sets_user_name():
    """Guardian name for a minor must update both guardian_name AND user_name."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Pedro Lima",
        patient_name="Pedro Lima",
        patient_cpf="111.222.333-44",
        patient_age=10,
        birth_date="15/03/2015",
        is_for_self=False,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual é o nome completo do responsável pelo paciente?"),
            HumanMessage(content="Ana Lima"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("guardian_name") == "Ana Lima"
    assert result.get("user_name") == "Ana Lima", "user_name must mirror guardian_name for minors"


async def test_collect_info_adult_skips_guardian_steps():
    """For an adult patient (age >= 18), guardian steps must be skipped entirely."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage
    from app.graph.schemas import CollectInfoOutput

    state = _base_minor_state(
        patient_age=30,
        birth_date="15/03/1994",
        is_for_self=True,  # contact is the patient — is_for_self question already answered
        messages=[
            AIMessage(content="O paciente já é paciente da clínica?"),
            HumanMessage(content="sim"),
        ],
    )
    collect_result = CollectInfoOutput(
        reply="Perfeito! Com qual médico prefere?",
        is_patient=True,
        is_complete=False,
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes._get_collect_llm") as mock_llm_fn:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=collect_result)
        mock_llm_fn.return_value = mock_llm
        result = await collect_info_node(state, {})

    # Guardian fields must NOT have been set
    assert result.get("guardian_name") is None
    assert result.get("guardian_cpf") is None


async def test_log_event_called_for_new_conversation():
    """log_event('conversation_started') must fire when snapshot is empty."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
             patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
            from app.main import process_message
            await process_message(PHONE, "oi")
            mock_log.assert_awaited_once_with("conversation_started", PHONE)
    finally:
        gg.chatbot = original


# ── Greeting injection for known patients ────────────────────────────────────

def _make_patient_agent_state(**overrides) -> dict:
    base = {
        "phone": PHONE,
        "stage": "patient_agent",
        "user_name": "Carlos",
        "patient_name": "Carlos Silva",
        "patient_age": 35,
        "birth_date": "10/05/1989",
        "is_patient": True,
        "is_returning_patient": True,
        "preferred_doctor": "julio",
        "patient_email": "carlos@email.com",
        "is_for_self": True,
        "guardian_relationship": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "silent_mode": None,
        "user_db_id": None,
        "messages": [HumanMessage(content="quero agendar uma consulta")],
    }
    base.update(overrides)
    return base


async def _run_patient_agent(state: dict, last_assistant_time=None) -> "SystemMessage":
    """Helper: run patient_agent_node and return the SystemMessage passed to the LLM."""
    from app.graph.nodes import patient_agent_node
    from langchain_core.messages import SystemMessage

    ai_response = MagicMock()
    ai_response.tool_calls = []
    ai_response.content = "resposta"

    captured = []

    async def fake_ainvoke(messages):
        captured.extend(messages)
        return ai_response

    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=last_assistant_time), \
         patch("app.google_calendar.format_doctor_schedules", return_value="seg-sex"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        await patient_agent_node(state, {})

    return next((m for m in captured if isinstance(m, SystemMessage)), None)


async def test_patient_agent_injects_greeting_on_first_turn():
    """No prior AI messages → greeting instruction injected."""
    state = _make_patient_agent_state(messages=[HumanMessage(content="quero agendar")])
    system_msg = await _run_patient_agent(state, last_assistant_time=None)
    assert system_msg is not None
    assert "INÍCIO DE CONVERSA" in system_msg.content
    assert "Carlos" in system_msg.content


async def test_patient_agent_injects_greeting_on_new_day():
    """Prior AI messages exist but last assistant message was on a previous day → greeting injected."""
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo

    yesterday = datetime.now(ZoneInfo("America/Recife")) - timedelta(days=1)
    yesterday_utc = yesterday.astimezone(timezone.utc)

    state = _make_patient_agent_state(messages=[
        AIMessage(content="Boa tarde, Carlos!"),
        HumanMessage(content="oi de novo"),
    ])
    system_msg = await _run_patient_agent(state, last_assistant_time=yesterday_utc)
    assert system_msg is not None
    assert "INÍCIO DE CONVERSA" in system_msg.content
    assert "Carlos" in system_msg.content


async def test_patient_agent_no_greeting_injection_on_same_day():
    """Prior AI messages exist and last assistant message was today → no greeting injected."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    today_utc = datetime.now(timezone.utc)

    state = _make_patient_agent_state(messages=[
        AIMessage(content="Boa tarde, Carlos!"),
        HumanMessage(content="qual o horário disponível?"),
    ])
    system_msg = await _run_patient_agent(state, last_assistant_time=today_utc)
    assert system_msg is not None
    assert "INÍCIO DE CONVERSA" not in system_msg.content


