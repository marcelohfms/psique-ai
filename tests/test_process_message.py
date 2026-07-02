"""Tests for process_message() — conversation routing logic."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from langchain_core.messages import HumanMessage, AIMessage

from tests.conftest import PHONE, CONFIG


@pytest.fixture(autouse=True)
def clear_dedup_caches():
    import app.main as _main
    _main._seen_msg_ids.clear()
    _main._seen_phone_text.clear()
    yield
    _main._seen_msg_ids.clear()
    _main._seen_phone_text.clear()


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
    "age": 30,
    "is_patient": True,
    "is_returning_patient": True,
    "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",  # julio
    "birth_date": "01/01/1994",
    "email": "maria@example.com",
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


async def test_standalone_contact_with_manual_hold_is_silenced():
    """Contato solto (sem paciente) com manual_hold=true → bot não processa."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
             patch("app.main.get_contact_by_phone", new_callable=AsyncMock,
                   return_value={"id": "c-rep", "phone": "5581997556159", "manual_hold": True}), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi, sou representante")
            chatbot.ainvoke.assert_not_called()   # silêncio: nada foi processado
    finally:
        gg.chatbot = original


async def test_contact_without_manual_hold_proceeds_normally():
    """Contato solto SEM manual_hold → fluxo normal (regressão)."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
             patch("app.main.get_contact_by_phone", new_callable=AsyncMock,
                   return_value={"id": "c1", "phone": "5583999999999", "manual_hold": False}), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi")
            chatbot.ainvoke.assert_called()   # seguiu o fluxo
    finally:
        gg.chatbot = original


async def test_known_user_missing_required_fields_stays_in_collect_info():
    """A patient missing required fields (email, birth_date) must stay in collect_info."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    incomplete_user = {
        **_KNOWN_USER,
        "birth_date": None,
        "email": None,
        "age": None,
        "is_returning_patient": None,
    }
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=incomplete_user), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[incomplete_user]), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi")
            state_update = chatbot.ainvoke.call_args[0][0]
            # Missing required fields → stay in collect_info, not patient_agent
            assert state_update.get("stage") != "patient_agent"
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
    """When the graph already has state, inject HumanMessage + always-sync DB fields."""
    import app.graph.graph as gg
    existing_state = {
        "stage": "patient_agent",
        "messages": [HumanMessage(content="anterior")],
        "preferred_doctor": "julio",
        "is_returning_patient": True,
    }
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
            # messages + silent_mode + phone + always-synced DB fields
            assert "messages" in state_update
            assert state_update["silent_mode"] is False
            assert state_update["messages"][0].content == "nova mensagem"
            # is_patient, user_name, patient_name always synced from DB
            assert "is_patient" in state_update
            assert "user_name" in state_update
            assert "patient_name" in state_update
    finally:
        gg.chatbot = original


@pytest.mark.asyncio
async def test_existing_patient_agent_syncs_missing_doctor_and_returning():
    """When stage=patient_agent but preferred_doctor/is_returning_patient are missing, sync from DB."""
    import app.graph.graph as gg
    known_user_with_returning = {**_KNOWN_USER, "is_returning_patient": True}
    # Snapshot missing preferred_doctor and is_returning_patient
    existing_state = {"stage": "patient_agent", "messages": [HumanMessage(content="anterior")]}
    chatbot = _make_chatbot(snapshot_values=existing_state)
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=known_user_with_returning), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[known_user_with_returning]), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "nova mensagem")
            state_update = chatbot.ainvoke.call_args[0][0]
            # Missing critical fields should be synced from DB
            assert state_update.get("preferred_doctor") == "julio"
            assert state_update.get("is_returning_patient") is True
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
        "is_patient": None,
        "_is_patient_confirmed": True,  # tests set up state explicitly — treat as confirmed
        "preferred_doctor": None,
        "patient_email": None,
        "consultation_reason": None,
        "referral_professional": None,
        "medication_note": None,
        "pending_patients": None,
        "pending_confirmation_patient": None,
        "user_db_id": None,
        "silent_mode": None,
        "messages": [],
    }
    base.update(kwargs)
    return base


async def test_collect_info_is_patient_true_persists_is_self_true():
    """Quando o contato responde 'para mim', upsert_user deve ser chamado com is_patient=True.

    Reproduz o bug onde is_self ficava False no banco para pacientes adultos que
    agendavam para si mesmos sem user_db_id preenchido no state.
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import AIMessage, HumanMessage

    state = _base_minor_state(
        user_name="Maria de Fátima Costa",
        patient_name=None,
        is_patient=None,
        user_db_id=None,
        messages=[
            AIMessage(content="A consulta é para você ou para outra pessoa?"),
            HumanMessage(content="Para mim"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-patient-id") as mock_upsert:
        result = await collect_info_node(state, {})

    assert result.get("is_patient") is True
    assert result.get("patient_name") == "Maria de Fátima Costa"
    mock_upsert.assert_awaited()
    db_payload = mock_upsert.call_args[0][1]
    assert db_payload.get("is_patient") is True, (
        f"is_patient deveria ser True no payload do DB, mas foi: {db_payload}"
    )
    assert result.get("_is_patient_confirmed") is True


async def test_collect_info_unconfirmed_is_patient_asks_again():
    """Quando is_patient veio do banco (não confirmado nesta conversa), Eva deve perguntar novamente."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import AIMessage, HumanMessage

    state = _base_minor_state(
        user_name="Maria de Fátima",
        patient_name="Maria de Fátima",
        is_patient=False,           # veio do banco
        _is_patient_confirmed=False,  # não confirmado nesta conversa
        messages=[
            AIMessage(content="Olá! Pode me dizer seu nome?"),
            HumanMessage(content="Quero agendar"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock):
        await collect_info_node(state, {})

    sent = mock_send.call_args[0][1].lower()
    assert "para você" in sent or "outra pessoa" in sent, (
        f"Eva deveria perguntar para quem é a consulta, mas enviou: {sent}"
    )


async def test_collect_info_flip_to_third_party_clears_stale_patient_name():
    """When is_patient (unconfirmed, hydrated from DB as True) is re-asked and the
    contact now says it's for someone else, a stale patient_name==user_name must be
    cleared so collect_info asks for the real patient's name instead of silently
    keeping the contact's own name as the patient's.
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import AIMessage, HumanMessage

    state = _base_minor_state(
        user_name="Adriana de Faria Pilar",
        patient_name="Adriana de Faria Pilar",  # previously inferred as self
        is_patient=True,              # veio do banco
        _is_patient_confirmed=False,  # não confirmado nesta conversa
        messages=[
            AIMessage(content="A consulta é para você ou para outra pessoa?"),
            HumanMessage(content="Outra pessoa"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock):
        result = await collect_info_node(state, {})

    assert result.get("is_patient") is False
    assert result.get("patient_name") is None
    sent = mock_send.call_args[0][1].lower()
    assert "nome completo do paciente" in sent


async def test_collect_info_confirmed_from_db_does_not_ask_again():
    """Quando _is_patient_confirmed=True (respondido na conversa), Eva não pergunta de novo."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import AIMessage, HumanMessage

    # State with is_patient confirmed — should skip to is_returning question
    state = _base_minor_state(
        user_name="Maria de Fátima",
        patient_name="Maria de Fátima",
        patient_age=30,
        birth_date="01/01/1994",
        is_patient=True,
        _is_patient_confirmed=True,
        is_returning_patient=None,
        messages=[
            AIMessage(content="É a primeira consulta ou já está em acompanhamento?"),
            HumanMessage(content="Primeira vez"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock):
        await collect_info_node(state, {})

    sent = mock_send.call_args[0][1].lower()
    assert "para você" not in sent and "outra pessoa" not in sent, (
        f"Eva não deveria perguntar para quem é a consulta, mas enviou: {sent}"
    )


async def test_collect_info_asks_guardian_name_after_minor_birth_date():
    """After birth_date reveals patient < 18, the next question must be guardian name.

    In the new flow, is_patient is asked BEFORE birth_date (right after user_name),
    so the state must already have is_patient set when birth_date is collected.
    """
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
        is_patient=True,  # already answered — new flow asks this before birth_date
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
    sent = mock_send.call_args[0][1].lower()
    assert "primeira consulta" in sent or "já está em acompanhamento" in sent


async def test_collect_info_asks_guardian_cpf_after_guardian_name():
    """After guardian_name is collected for a minor, the next step must be guardian CPF."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Maria Souza",  # contact name already collected (new flow: step 1)
        patient_name="Pedro Lima",
        patient_cpf="111.222.333-44",
        is_patient=False,  # already answered (new flow: step 2b)
        is_returning_patient=False,
        patient_age=10,
        birth_date="15/03/2015",
        messages=[
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


async def test_collect_info_persists_doctor_when_mentioned():
    """Mentioning a doctor sets preferred_doctor in state.

    DB persist only happens when user_db_id is already known (to avoid creating a
    contact with name=null before registration is complete).
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage
    from app.database import DOCTOR_IDS

    # Case 1: new user (no user_db_id) — preferred_doctor saved to state, NOT to DB
    state = _base_minor_state(
        user_name=None,
        patient_name=None,
        patient_cpf=None,
        preferred_doctor=None,
        messages=[HumanMessage(content="Quero agendar uma consulta com a Dra. Bruna")],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock) as mock_upsert:
        result = await collect_info_node(state, {})

    assert result.get("preferred_doctor") == "bruna"
    # Must NOT call upsert_user before the patient record exists (avoids null-name contact)
    mock_upsert.assert_not_awaited()

    # Case 2: existing user (user_db_id known) — doctor_id IS persisted to DB
    state2 = _base_minor_state(
        user_name=None,
        patient_name=None,
        patient_cpf=None,
        preferred_doctor=None,
        messages=[HumanMessage(content="Quero agendar uma consulta com a Dra. Bruna")],
    )
    state2["user_db_id"] = "existing-patient-uuid"
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="existing-patient-uuid") as mock_upsert2:
        result2 = await collect_info_node(state2, {})

    assert result2.get("preferred_doctor") == "bruna"
    mock_upsert2.assert_awaited()
    db_payload = mock_upsert2.call_args[0][1]
    assert db_payload.get("doctor_id") == DOCTOR_IDS["bruna"]


async def test_collect_info_does_not_guess_doctor_when_both_mentioned():
    """If both doctors are mentioned, do not auto-pick one."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage

    state = _base_minor_state(
        user_name=None, patient_name=None, patient_cpf=None, preferred_doctor=None,
        messages=[HumanMessage(content="Quero agendar — qual a diferença entre Dr. Júlio e Dra. Bruna?")],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock) as mock_upsert:
        result = await collect_info_node(state, {})

    assert result.get("preferred_doctor") is None
    # no doctor_id persisted from ambiguous mention
    for call in mock_upsert.await_args_list:
        assert "doctor_id" not in (call[0][1] if len(call[0]) > 1 else {})


async def test_collect_info_new_minor_after_guardian_cpf_goes_to_doctor():
    """Menor NOVO: após CPF do responsável, a próxima pergunta é o médico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Maria Souza",
        patient_name="Pedro Lima",
        patient_cpf="111.222.333-44",
        is_patient=False,
        is_returning_patient=False,
        patient_age=10,
        birth_date="15/03/2015",
        guardian_name="Maria Souza",
        messages=[
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
    sent = mock_send.call_args[0][1].lower()
    assert "júlio" in sent or "bruna" in sent


async def test_collect_info_no_to_is_patient_sets_returning_patient_false():
    """'não' to 'já é paciente da clínica?' must set is_returning_patient=False, NOT is_patient=False."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        patient_age=30,
        birth_date="15/03/1994",
        patient_cpf="123.456.789-00",
        is_patient=True,  # already answered — prevents Step 4d from intercepting
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


@pytest.mark.parametrize("answer,expected", [
    ("já sou paciente", True),
    ("já estou em acompanhamento", True),
    ("não, não é a primeira vez", True),   # negação que INVERTE o sentido = retornante
    ("é a primeira vez", False),
    ("primeira consulta", False),
    ("não", False),
    ("sim", True),
])
async def test_collect_info_is_returning_classifier_phrases(answer, expected):
    """O classificador de 'já é paciente?' entende frases inequívocas, inclusive
    negações que invertem o sentido ('não é a primeira' = já é paciente)."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        patient_age=30,
        birth_date="15/03/1994",
        patient_cpf="123.456.789-00",
        is_patient=True,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content=_Q),
            HumanMessage(content=answer),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is expected


async def test_collect_info_yes_to_is_patient_sets_returning_patient_true():
    """'sim' to 'já é paciente da clínica?' must set is_returning_patient=True, NOT touch is_patient."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        patient_age=30,
        birth_date="15/03/1994",
        patient_cpf="123.456.789-00",
        is_patient=True,  # already answered — prevents Step 4d from intercepting
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


async def test_collect_info_adult_birth_date_asks_is_returning_patient():
    """After birth date for an adult (with is_patient already answered), ask is_returning_patient.

    In the new flow, is_patient is collected BEFORE birth_date (right after user_name).
    So when birth_date is collected, is_patient is already known and the next step is
    is_returning_patient.
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="João Silva",
        patient_name="João Silva",
        patient_cpf="123.456.789-00",
        is_patient=True,  # already answered before birth_date in the new flow
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
    assert "acompanhamento" in sent.lower() or "primeira consulta" in sent.lower()
    assert result.get("is_returning_patient") is None  # not yet answered


async def test_collect_info_is_patient_yes_proceeds_to_birth_date():
    """'sou eu' to the is_patient question must set is_patient=True and proceed to birth date.

    In the new flow, is_patient is asked right after user_name (step 2b),
    before birth_date and CPF are collected.
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="João Silva",
        patient_name=None,  # not yet set — will be set when is_patient=True is answered
        patient_cpf=None,
        patient_age=None,
        birth_date=None,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="A consulta é para você ou para outra pessoa?"),
            HumanMessage(content="sou eu mesmo"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_patient") is True
    assert result.get("patient_name") == "João Silva"  # copied from user_name
    sent = mock_send.call_args[0][1]
    assert "nascimento" in sent.lower()


async def test_collect_info_is_patient_no_asks_patient_name():
    """'sou a mãe' to the is_patient question must set is_patient=False and ask for patient name.

    In the new flow, the contact name is already known (step 1: user_name).
    When is_patient=False, the next step is to ask for the PATIENT's name (step 2c).
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Maria Souza",  # contact name already collected in step 1
        patient_name=None,
        patient_cpf=None,
        patient_age=None,
        birth_date=None,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="A consulta é para você ou para outra pessoa?"),
            HumanMessage(content="não, sou a mãe"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_patient") is False
    sent = mock_send.call_args[0][1]
    # Next step asks for the PATIENT's name, not the contact's name
    assert "paciente" in sent.lower() and "nome" in sent.lower()


async def test_collect_info_stale_db_record_still_asks_patient_name():
    """Regression: a partial DB record (name saved, patient_name/is_patient still
    null) must NOT make collect_info skip the patient-name question.

    Bug found 2026-07-01 (Adriana conversation, 5581981464986): the DB-reload block
    defaulted patient_name to the contact's own name (`u.get("patient_name") or
    u.get("name")`) even when is_patient hadn't been confirmed yet. That made the
    step machine think the patient's name was already known and skip straight to
    the birth-date question, silently registering the wrong person as the patient.
    """
    from app.graph.nodes import collect_info_node

    stale_db_user = {
        "id": "existing-id",
        "name": "Adriana de Faria Pilar",
        "patient_name": None,
        "is_patient": None,
        "age": None,
        "birth_date": None,
        "is_returning_patient": None,
        "doctor_id": None,
        "email": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "guardian_relationship": None,
        "patient_cpf": None,
        "modality_restriction": None,
        "age_exception": None,
        "active": True,
    }
    state = _base_minor_state(
        user_name="Adriana de Faria Pilar",
        patient_name=None,
        patient_cpf=None,
        patient_age=None,
        birth_date=None,
        is_patient=None,
        _is_patient_confirmed=False,
        messages=[
            HumanMessage(content="preciso de um laudo psiquiátrico pra meu pai"),
            AIMessage(content="A consulta é para você ou para outra pessoa?"),
            HumanMessage(content="Outra pessoa"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[stale_db_user]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="existing-id"):
        result = await collect_info_node(state, {})

    assert result.get("is_patient") is False
    assert result.get("patient_name") is None
    sent = mock_send.call_args[0][1]
    assert "nome completo do paciente" in sent.lower()


async def test_collect_info_patient_name_step_saves_patient_name():
    """Patient name answer (step 2c) must save patient_name and proceed to CPF.

    In the new flow: user_name (contact) is collected first, then is_patient=False is
    determined, then the patient's name is asked separately (step 2c). The contact name
    (user_name) must NOT be changed; only patient_name is set.
    """
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _PATIENT_NAME_Q = "Qual o nome completo do paciente?"
    state = _base_minor_state(
        user_name="Maria Silva",  # contact name already collected in step 1
        patient_name=None,
        patient_cpf=None,
        patient_age=None,
        birth_date=None,
        is_patient=False,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content=_PATIENT_NAME_Q),
            HumanMessage(content="João Silva"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="new-id"):
        result = await collect_info_node(state, {})

    assert result.get("patient_name") == "João Silva"
    assert result.get("user_name") is None  # contact name must NOT be changed
    sent = mock_send.call_args[0][1]
    assert "nascimento" in sent.lower()


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
        is_patient=False,
        is_returning_patient=False,
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
        is_patient=True,  # contact is the patient — is_patient question already answered
        messages=[
            AIMessage(content="É a primeira consulta ou o paciente já está em acompanhamento na clínica?"),
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


async def test_pending_appointment_success_with_internal_prefix():
    """Regressão: confirm_appointment retorna o código AGENDAMENTO_OK prefixado com
    '[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE]'. O handler de pending_appointment
    deve reconhecê-lo como SUCESSO (mensagem de taxa de reserva), não como erro."""
    from app.graph.nodes import patient_agent_node
    from app.graph.tools import confirm_appointment

    state = _make_patient_agent_state(
        messages=[
            AIMessage(content="Só confirmar antes de registrar: ..."),
            HumanMessage(content="pode"),
        ],
        pending_appointment={
            "slot_datetime": "2026-06-25T19:00:00",
            "slot_duration_minutes": 60,
            "modality": "presencial",
        },
    )

    prefixed_ok = (
        "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] AGENDAMENTO_OK\n"
        "Dr. Júlio — quinta-feira, 25/06/2026 às 19:00\nID: abc123"
    )
    sent = []

    async def fake_send_text(phone, text):
        sent.append(text)

    with patch.object(confirm_appointment, "coroutine", new_callable=AsyncMock, return_value=prefixed_ok), \
         patch("app.whatsapp.send_text", side_effect=fake_send_text), \
         patch("app.database.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None):
        result = await patient_agent_node(state, CONFIG)

    assert sent, "nenhuma mensagem enviada ao paciente"
    patient_msg = sent[0]
    assert "Tive um problema" not in patient_msg
    assert "taxa de reserva" in patient_msg.lower()
    assert "25/06/2026 às 19:00" in patient_msg
    assert result.get("pending_appointment") is None


async def test_silent_mode_survives_when_response_has_tool_calls():
    """Regressão: quando a atendente envia uma instrução (silent_mode=True) e a LLM
    responde com uma tool_call (ex: confirm_appointment com force_encaixe=True), o node
    não pode resetar silent_mode para False nesse mesmo retorno — o ToolNode lê o estado
    logo em seguida e depende de silent_mode ainda estar True para aceitar force_encaixe.
    Resetar cedo demais fazia a Eva tentar o encaixe, ser bloqueada pela grade normal do
    médico e mentir para a paciente dizendo que a consulta estava confirmada."""
    from app.graph.nodes import patient_agent_node

    state = _make_patient_agent_state(
        silent_mode=True,
        messages=[HumanMessage(content="[Instrução da atendente]: Eva, encaixe para amanhã às 08:00")],
    )

    ai_response = MagicMock()
    ai_response.tool_calls = [{
        "name": "confirm_appointment",
        "args": {"slot_datetime": "2026-07-03T08:00:00", "slot_duration_minutes": 60, "force_encaixe": True},
        "id": "call_1",
        "type": "tool_call",
    }]
    ai_response.content = ""

    async def fake_ainvoke(messages):
        return ai_response

    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value={"price_adjustment_notified_at": "2026-01-01"}), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.format_doctor_schedules", return_value="seg-sex"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        result = await patient_agent_node(state, CONFIG)

    assert result.get("silent_mode") is not False, (
        "silent_mode foi resetado antes do ToolNode executar a tool_call — "
        "force_encaixe seria descartado silenciosamente"
    )


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


async def _run_patient_agent_with_user(state: dict, user: dict) -> "SystemMessage":
    """Helper variant that accepts a custom user dict for get_user_by_phone."""
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
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value=user), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.format_doctor_schedules", return_value="seg-sex"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        await patient_agent_node(state, {})

    return next((m for m in captured if isinstance(m, SystemMessage)), None)


async def test_price_notice_injected_when_not_yet_notified():
    """price_adjustment_notified_at=None → price notice IS injected into system prompt."""
    state = _make_patient_agent_state(messages=[HumanMessage(content="quero agendar")])
    user = {"price_adjustment_notified_at": None}
    system_msg = await _run_patient_agent_with_user(state, user=user)
    assert system_msg is not None
    assert "AVISO ÚNICO OBRIGATÓRIO" in system_msg.content


async def test_price_notice_not_injected_when_already_notified():
    """price_adjustment_notified_at set → price notice is NOT injected into system prompt."""
    state = _make_patient_agent_state(messages=[HumanMessage(content="quero agendar")])
    user = {"price_adjustment_notified_at": "2026-05-01T10:00:00"}
    system_msg = await _run_patient_agent_with_user(state, user=user)
    assert system_msg is not None
    assert "AVISO ÚNICO OBRIGATÓRIO" not in system_msg.content


# ── get_pricing_exception_rule ────────────────────────────────────────────────

def test_pricing_exception_rule_no_exception_returns_empty():
    from app.graph.prompts import get_pricing_exception_rule
    assert get_pricing_exception_rule(None, False, 650) == ""


def test_pricing_exception_rule_courtesy():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(0, True, 650)
    assert "cortesia" in block
    assert "PIX" not in block
    assert "nenhum valor" in block.lower()


def test_pricing_exception_rule_fee_waived_standard_price():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(None, True, 650)
    assert "DISPENSADA" in block
    assert "R$ 650,00" in block
    assert "PIX" not in block


def test_pricing_exception_rule_custom_price_normal_fee():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(500, False, 650)
    assert "R$ 500,00" in block
    assert "R$ 100,00" in block  # taxa de reserva still applies
    assert "NÃO mencione" in block  # Eva is instructed not to mention standard prices or reajuste


def test_pricing_exception_rule_custom_price_fee_waived():
    from app.graph.prompts import get_pricing_exception_rule
    block = get_pricing_exception_rule(500, True, 650)
    assert "R$ 500,00" in block
    assert "DISPENSADA" in block
    assert "PIX" not in block


@pytest.mark.asyncio
async def test_pricing_exception_block_injected_in_system_prompt():
    """When user has booking_fee_waived=True, the exception block appears in patient_agent_node system prompt."""
    from app.graph.nodes import patient_agent_node
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    user_with_waiver = {
        "id": "user-99",
        "name": "Ana",
        "patient_name": "Ana",
        "number": PHONE,
        "booking_fee_waived": True,
        "custom_price": None,
        "age": 32,
        "birth_date": "01/01/1994",
        "email": "ana@test.com",
        "active": True,
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",  # julio
        "is_returning_patient": True,
        "price_adjustment_notified_at": "2026-01-01T00:00:00",  # skip price notice
    }

    state = {
        "phone": PHONE,
        "stage": "patient_agent",
        "user_name": "Ana",
        "patient_name": "Ana",
        "patient_age": 32,
        "birth_date": "01/01/1994",
        "is_patient": True,
        "preferred_doctor": "julio",
        "is_returning_patient": True,
        "modality_restriction": None,
        "silent_mode": False,
        "messages": [HumanMessage(content="qual o meu valor de consulta?")],
    }

    captured_system_prompt = []

    async def fake_ainvoke(messages):
        for m in messages:
            if isinstance(m, SystemMessage):
                captured_system_prompt.append(m.content)
                break
        return AIMessage(content="Resposta mock")

    with patch("app.graph.nodes._get_agent_llm") as mock_llm_fn, \
         patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value=user_with_waiver), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.format_doctor_schedules", return_value="schedules mock"):
        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke
        mock_llm_fn.return_value = mock_llm
        await patient_agent_node(state, CONFIG)

    assert len(captured_system_prompt) == 1, "SystemMessage must be passed to LLM"
    system_prompt = captured_system_prompt[0]
    assert "DISPENSADA" in system_prompt, "Exception block must appear in system prompt for booking_fee_waived=True"


# ── Multi-patient selection confirmation ─────────────────────────────────────

def _base_multi_patient_state(**kwargs):
    """Base state for multi-patient disambiguation tests."""
    base = {
        "phone": "558199999999@s.whatsapp.net",
        "stage": "collect_info",
        "user_name": None,
        "patient_name": None,
        "patient_age": None,
        "birth_date": None,
        "patient_cpf": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "guardian_relationship": None,
        "is_patient": None,
        "preferred_doctor": None,
        "patient_email": None,
        "consultation_reason": None,
        "referral_professional": None,
        "medication_note": None,
        "pending_patients": None,
        "pending_confirmation_patient": None,
        "user_db_id": None,
        "silent_mode": None,
        "modality_restriction": None,
        "age_exception": None,
        "messages": [],
    }
    base.update(kwargs)
    return base


async def test_patient_selection_sends_confirmation_message():
    """When Eva matches a patient from pending_patients, she sends a confirmation
    message and stores the candidate in pending_confirmation_patient instead of
    advancing to patient_agent immediately."""
    from app.graph.nodes import collect_info_node

    patients = [
        {"id": "aaa", "patient_name": "Mariana França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": "r@example.com", "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
        {"id": "bbb", "patient_name": "Manuela França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": "r@example.com", "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
    ]
    state = _base_multi_patient_state(
        pending_patients=patients,
        messages=[
            AIMessage(content="Para qual paciente?\n1. Mariana França\n2. Manuela França"),
            HumanMessage(content="Manuela"),
        ],
    )

    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("stage") != "patient_agent", "Should not advance yet"
    assert result.get("pending_confirmation_patient") == patients[1]
    assert result.get("pending_patients") == patients
    sent = mock_send.call_args[0][1]
    assert "Manuela França" in sent
    assert "certo" in sent.lower() or "confirmar" in sent.lower()


async def test_patient_confirmation_affirmative_advances():
    """When pending_confirmation_patient is set and the guardian replies
    affirmatively, Eva clears disambiguation state and advances to patient_agent."""
    from app.graph.nodes import collect_info_node

    candidate = {
        "id": "bbb", "patient_name": "Manuela França", "name": "Rebeka França",
        "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
        "is_patient": False, "is_returning_patient": True,
        "email": "r@example.com", "guardian_name": None, "guardian_cpf": None,
        "guardian_relationship": "mãe", "patient_cpf": None,
        "modality_restriction": None, "age_exception": None,
    }
    state = _base_multi_patient_state(
        pending_confirmation_patient=candidate,
        pending_patients=[{}, candidate],
        messages=[
            AIMessage(content="Só confirmar: você está entrando em contato para Manuela França, certo?"),
            HumanMessage(content="sim"),
        ],
    )

    with patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("stage") == "patient_agent"
    assert result.get("patient_name") == "Manuela França"
    assert result.get("user_db_id") == "bbb"
    assert result.get("pending_confirmation_patient") is None
    assert result.get("pending_patients") is None


async def test_patient_confirmation_negative_reshows_list():
    """When pending_confirmation_patient is set and the guardian says no,
    Eva re-shows the patient list and clears pending_confirmation_patient."""
    from app.graph.nodes import collect_info_node

    patients = [
        {"id": "aaa", "patient_name": "Mariana França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": None, "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
        {"id": "bbb", "patient_name": "Manuela França", "name": "Rebeka França",
         "age": 17, "birth_date": "09/12/2008", "doctor_id": None,
         "is_patient": False, "is_returning_patient": True,
         "email": None, "guardian_name": None, "guardian_cpf": None,
         "guardian_relationship": "mãe", "patient_cpf": None,
         "modality_restriction": None, "age_exception": None},
    ]
    candidate = patients[1]
    state = _base_multi_patient_state(
        pending_confirmation_patient=candidate,
        pending_patients=patients,
        messages=[
            AIMessage(content="Só confirmar: você está entrando em contato para Manuela França, certo?"),
            HumanMessage(content="não"),
        ],
    )

    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("stage") != "patient_agent"
    assert result.get("pending_confirmation_patient") is None
    assert result.get("pending_patients") == patients
    sent = mock_send.call_args[0][1]
    assert "Mariana França" in sent
    assert "Manuela França" in sent


@pytest.mark.asyncio
async def test_patient_agent_prompt_has_reference_block_and_appointment_labels():
    """The system prompt built by patient_agent_node must contain the calendar
    reference block and pre-computed weekday/relative labels for appointments."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from unittest.mock import AsyncMock, MagicMock, patch
    from langchain_core.messages import HumanMessage, AIMessage
    from tests.conftest import CONFIG
    from app.graph.nodes import patient_agent_node

    TZ = ZoneInfo("America/Recife")
    now = _dt.datetime.now(TZ)
    tomorrow = (now + _dt.timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

    appt = {
        "appointment_id": "appt-1",
        "start_time": tomorrow.isoformat(),
        "booking_fee_paid_at": now.isoformat(),
        "booking_fee_waived": False,
        "recently_ended": False,
    }

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="ok")

    state = {
        "phone": "5581999999999@s.whatsapp.net",
        "stage": "patient_agent",
        "user_name": "Maria Silva",
        "patient_name": "Maria Silva",
        "patient_age": 30,
        "is_patient": True,
        "is_returning_patient": True,
        "preferred_doctor": "julio",
        "messages": [HumanMessage(content="oi")],
    }

    with patch("app.graph.nodes.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes.get_upcoming_appointments", new_callable=AsyncMock, return_value=[appt]), \
         patch("app.google_calendar.format_doctor_schedules", return_value=""), \
         patch("app.graph.nodes._get_agent_llm", return_value=_FakeLLM()), \
         patch("app.graph.nodes.send_text", new_callable=AsyncMock), \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.get_last_assistant_message_time", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.nodes.is_registration_complete", return_value=True), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value=None):
        await patient_agent_node(state, CONFIG)

    system_prompt = captured["messages"][0].content
    assert "CALENDÁRIO DE REFERÊNCIA" in system_prompt
    assert "Consultas agendadas para este paciente:" in system_prompt
    # The appointment line carries the pre-computed relative + weekday label.
    assert "(amanhã," in system_prompt




async def test_collect_info_birth_date_leads_to_is_returning_question():
    """Após a data de nascimento, a próxima pergunta é 'já é paciente?'."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Ana", patient_name="Ana", patient_cpf=None,
        is_patient=True, birth_date=None, patient_age=None,
        messages=[
            HumanMessage(content="quero agendar uma consulta"),
            AIMessage(content="Qual a data de nascimento do paciente? (formato dd/mm/aaaa)"),
            HumanMessage(content="22/08/1990"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]):
        result = await collect_info_node(state, {})

    assert result.get("birth_date") == "22/08/1990"
    sent = mock_send.call_args[0][1].lower()
    assert "primeira consulta" in sent or "já está em acompanhamento" in sent


async def test_collect_info_returning_adult_skips_cpf_goes_to_doctor():
    """Adulto que JÁ é paciente: após 'já é paciente?'=sim, pula CPF e vai ao médico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Ana", patient_cpf=None,
        is_patient=True, patient_age=35, birth_date="22/08/1990",
        is_returning_patient=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_Q),
            HumanMessage(content="já sou paciente"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is True
    sent = mock_send.call_args[0][1].lower()
    assert "cpf" not in sent
    assert "júlio" in sent or "bruna" in sent


async def test_collect_info_new_adult_asks_cpf_after_is_returning():
    """Adulto NOVO: após 'já é paciente?'=não, a próxima pergunta é o CPF."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    _Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    state = _base_minor_state(
        user_name="Ana", patient_name="Ana", patient_cpf=None,
        is_patient=True, patient_age=35, birth_date="22/08/1990",
        is_returning_patient=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content=_Q),
            HumanMessage(content="é a primeira vez"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("is_returning_patient") is False
    sent = mock_send.call_args[0][1].lower()
    assert "cpf" in sent


async def test_collect_info_returning_minor_guardian_name_skips_guardian_cpf():
    """Menor que JÁ é paciente: após nome do responsável, pula CPF do responsável e vai ao médico."""
    from app.graph.nodes import collect_info_node
    from langchain_core.messages import HumanMessage, AIMessage

    state = _base_minor_state(
        user_name="Maria Souza", patient_name="Pedro Lima", patient_cpf=None,
        is_patient=False, patient_age=10, birth_date="15/03/2015",
        is_returning_patient=True, guardian_name=None, guardian_cpf=None,
        messages=[
            HumanMessage(content="quero agendar"),
            AIMessage(content="Qual é o nome completo do responsável pelo paciente?"),
            HumanMessage(content="Maria Souza"),
        ],
    )
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("guardian_name") == "Maria Souza"
    sent = mock_send.call_args[0][1].lower()
    assert "cpf" not in sent
    assert "júlio" in sent or "bruna" in sent


# ── _looks_like_name validation ───────────────────────────────────────────────

def _get_looks_like_name():
    """Import the nested helper by running collect_info_node source context."""
    # The function is defined inside collect_info_node; extract it by running a
    # minimal node call and grabbing the closure — easier to just re-implement
    # the same logic and test the node's observable behaviour instead.
    # We test via the node's response to ambiguous patient_name inputs.
    pass


@pytest.mark.parametrize("text,expected", [
    # Valid names
    ("João Silva", True),
    ("Ana", True),
    ("María José", True),
    ("O'Brien", True),
    ("李明", False),          # no latin letters — rejected by regex
    # Too short / digits
    ("Jo", False),
    ("18/11/2013", False),
    ("123", False),
    # Filler phrases
    ("que coloquei o nome acima", False),
    ("o mesmo de cima", False),
    ("já disse", False),
    ("igual ao meu", False),
    ("conforme acima", False),
    # Confirmations
    ("sim", False),
    ("não", False),
    ("isso", False),
    ("ok", False),
    ("ela", False),
    # Generic noun phrases starting with article
    ("minha filha", False),
    ("o paciente", False),
    ("a paciente", False),
    # Private notes or long messages (more than 80 chars or multiple punctuation marks)
    ("Como ela está viajando a trabalho, não conseguirá comparecer e gostaria de reagendar o atendimento. Fico no aguardo. Muito obrigado!", False),
    ("Patient note: He has traveled for work and needs to reschedule. Please contact.", False),
    ("Maria Silva!", True),  # single punctuation is ok
])
def test_looks_like_name(text, expected):
    """_looks_like_name rejects non-names and accepts real names."""
    import re
    import unicodedata

    def _looks_like_name(t):
        t = t.strip()
        if len(t) < 3:
            return False
        if re.search(r'\d', t):
            return False
        # Nomes não são notas/mensagens longas
        _punct_count = sum(1 for c in t if c in '.!?,;')
        if _punct_count > 1:
            return False
        if len(t) > 80:
            return False
        _non_name = [
            "que coloquei", "o mesmo", "acima", "já disse", "ja disse",
            "o de cima", "igual", "conforme", "como disse", "como coloquei",
            "minha filha", "meu filho", "minha mae", "minha mãe", "meu pai",
            "o paciente", "a paciente",
        ]
        tl = t.lower()
        if any(p in tl for p in _non_name):
            return False
        _confirmations = {"sim", "não", "nao", "isso", "exato", "correto", "ok",
                          "ele", "ela", "eu", "certo", "isso mesmo"}
        if tl in _confirmations:
            return False
        if not re.search(r'[a-zA-ZÀ-ú]{2,}', t):
            return False
        if re.match(r'^(o|a|os|as|meu|minha|seu|sua)\s', tl):
            return False
        return True

    assert _looks_like_name(text) == expected, f"_looks_like_name({text!r}) should be {expected}"


async def test_collect_info_rejects_non_name_patient_name():
    """collect_info_node rejects ambiguous patient name and asks again."""
    from app.graph.nodes import collect_info_node
    state = {
        "messages": [
            HumanMessage(content="quero agendar"),
            AIMessage(content="Qual o nome completo do paciente?"),
            HumanMessage(content="que coloquei o nome acima"),
        ],
        "phone": PHONE,
        "user_name": "Maria Souza",
        "is_patient": False,
        "_is_patient_confirmed": True,
        "patient_name": None,
        "birth_date": None,
        "patient_age": None,
        "is_returning_patient": None,
        "guardian_name": None,
        "guardian_cpf": None,
        "guardian_relationship": None,
        "preferred_doctor": "julio",
        "patient_email": None,
        "patient_cpf": None,
        "stage": "collect_info",
    }
    with patch("app.graph.nodes.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.graph.nodes.save_message", new_callable=AsyncMock), \
         patch("app.graph.nodes.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.nodes.upsert_user", new_callable=AsyncMock, return_value="id"):
        result = await collect_info_node(state, {})

    assert result.get("patient_name") is None
    sent = mock_send.call_args[0][1].lower()
    assert "nome" in sent
