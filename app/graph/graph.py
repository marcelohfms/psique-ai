from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

from app.graph.state import ConversationState
from app.graph.nodes import collect_info_node, patient_agent_node, TOOLS, RESUME_AFTER_TOOL
from app.database import is_registration_complete, DOCTOR_IDS


def _incoming_payment_receipt(state: ConversationState) -> bool:
    """True when the message being processed this turn is a payment receipt.

    Only the LAST message counts, and only if it came from the patient: older
    receipts in the history were already handled, and Eva's own messages quoting
    a receipt must never re-trigger this.
    """
    from app.media import is_payment_receipt_message

    messages = state.get("messages") or []
    if not messages:
        return False
    last = messages[-1]
    if getattr(last, "type", None) != "human":
        return False
    return is_payment_receipt_message(str(getattr(last, "content", "") or ""))


def _route_entry(state: ConversationState) -> str:
    stage = state.get("stage", "collect_info")

    # A payment receipt ALWAYS needs tools — register_payment resolves the patient
    # from the phone/appointment and never depends on the cadastro being complete.
    # Without this, a conversation whose registration is incomplete is pinned to
    # collect_info (a node with no tools): Eva reads the receipt, answers "recebemos
    # o comprovante!" and registers nothing, so the booking fee stays NULL and the
    # cron auto-cancels the consultation hours later (caso Bernardo Lima Beltrão
    # Teixeira, 5581987415206, 2026-07-31 — 43 conversations were in this state).
    if _incoming_payment_receipt(state):
        return "patient_agent"

    if stage == "patient_agent":
        # Safety guard: if registration is incomplete, always go through collect_info.
        # Prevents scheduling before all required fields are collected, even if stage
        # was prematurely set to patient_agent in a previous turn.
        _reg_check = {
            "name": state.get("user_name"),
            "email": state.get("patient_email"),
            "birth_date": state.get("birth_date"),
            "doctor_id": DOCTOR_IDS.get(state.get("preferred_doctor", ""), None),
            "is_patient": state.get("is_patient"),
            "is_returning_patient": state.get("is_returning_patient"),
            "patient_name": state.get("patient_name"),
            "age": state.get("patient_age"),
            "guardian_name": state.get("guardian_name"),
            "guardian_cpf": state.get("guardian_cpf"),
            "guardian_relationship": state.get("guardian_relationship"),
        }
        if not is_registration_complete(_reg_check):
            return "collect_info"
    return stage


def _route_after_collect(state: ConversationState) -> str:
    # If collect_info just completed, continue to patient_agent in the same turn
    # so tools (request_document, confirm_appointment, etc.) are called immediately.
    if state.get("stage") == "patient_agent":
        return "patient_agent"
    return END


def _route_patient_agent(state: ConversationState) -> str:
    """Route to tools if LLM returned tool calls, otherwise end the turn."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    # Guards do patient_agent_node que executam uma tool sem passar pela LLM
    # injetam (AIMessage com tool_call + ToolMessage) e marcam a ToolMessage com
    # RESUME_AFTER_TOOL quando a LLM ainda precisa agir sobre aquele resultado.
    # Sem esse desvio o turno terminava aqui — a última mensagem é a ToolMessage,
    # que não tem tool_calls — e a instrução interna morria sem leitor: a paciente
    # confirmava a remarcação e não recebia resposta nenhuma (caso Elisabete/Isaac,
    # 5581987385089, 02/08/2026).
    if getattr(last, "additional_kwargs", None) and last.additional_kwargs.get(RESUME_AFTER_TOOL):
        return "patient_agent"
    return END


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(ConversationState)

    g.add_node("collect_info", collect_info_node)
    g.add_node("patient_agent", patient_agent_node)
    g.add_node("tools", ToolNode(TOOLS))

    g.set_conditional_entry_point(
        _route_entry,
        {"collect_info": "collect_info", "patient_agent": "patient_agent"},
    )

    g.add_conditional_edges(
        "collect_info",
        _route_after_collect,
        {"patient_agent": "patient_agent", END: END},
    )

    # After tool execution, always return to patient_agent for the next LLM call
    g.add_conditional_edges(
        "patient_agent",
        _route_patient_agent,
        {"tools": "tools", "patient_agent": "patient_agent", END: END},
    )
    g.add_edge("tools", "patient_agent")

    cp = checkpointer or MemorySaver()
    return g.compile(checkpointer=cp)


# Default instance with in-memory checkpointer (replaced at startup when Supabase is available)
chatbot = build_graph()
