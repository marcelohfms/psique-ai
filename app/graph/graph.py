from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

from app.graph.state import ConversationState
from app.graph.nodes import collect_info_node, patient_agent_node, TOOLS
from app.database import is_registration_complete, DOCTOR_IDS


def _route_entry(state: ConversationState) -> str:
    stage = state.get("stage", "collect_info")
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
        {"tools": "tools", END: END},
    )
    g.add_edge("tools", "patient_agent")

    cp = checkpointer or MemorySaver()
    return g.compile(checkpointer=cp)


# Default instance with in-memory checkpointer (replaced at startup when Supabase is available)
chatbot = build_graph()
