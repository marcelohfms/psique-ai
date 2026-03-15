from datetime import datetime
from zoneinfo import ZoneInfo
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import ConversationState
from app.graph.schemas import CollectInfoOutput
from app.graph.tools import get_available_slots, confirm_appointment, request_document, transfer_to_human
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, ADULT_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM
from app.uazapi import send_text
from app.database import upsert_user, DOCTOR_IDS

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [get_available_slots, confirm_appointment, request_document, transfer_to_human]

_collect_llm = None
_agent_llm = None


def _get_collect_llm():
    global _collect_llm
    if _collect_llm is None:
        _collect_llm = ChatOpenAI(model="gpt-4o", temperature=0).with_structured_output(CollectInfoOutput)
    return _collect_llm


def _get_agent_llm():
    global _agent_llm
    if _agent_llm is None:
        _agent_llm = ChatOpenAI(model="gpt-4o", temperature=0).bind_tools(TOOLS)
    return _agent_llm


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def collect_info_node(state: ConversationState, config: RunnableConfig) -> dict:
    collected = {
        "user_name": state.get("user_name"),
        "is_for_self": state.get("is_for_self"),
        "patient_name": state.get("patient_name"),
        "patient_age": state.get("patient_age"),
        "is_patient": state.get("is_patient"),
        "preferred_doctor": state.get("preferred_doctor"),
    }

    messages = [
        SystemMessage(content=COLLECT_SYSTEM.format(collected=collected)),
        *state["messages"],
    ]

    result: CollectInfoOutput = await _get_collect_llm().ainvoke(messages)

    await send_text(state["phone"], result.reply)

    update: dict = {"messages": [AIMessage(content=result.reply)]}

    for field in ["user_name", "is_for_self", "patient_name", "patient_age", "is_patient", "preferred_doctor"]:
        val = getattr(result, field)
        if val is not None:
            update[field] = val

    if result.is_complete:
        update["stage"] = "patient_agent"

        # Merge collected fields with existing state for the upsert
        merged = {**state, **{k: v for k, v in update.items() if k not in ("messages", "stage")}}
        await upsert_user(state["phone"], {
            "name": merged.get("user_name"),
            "patient_name": merged.get("patient_name"),
            "age": merged.get("patient_age"),
            "is_patient": merged.get("is_patient"),
            "doctor_id": DOCTOR_IDS.get(merged.get("preferred_doctor", ""), None),
        })

    return update


async def patient_agent_node(state: ConversationState, config: RunnableConfig) -> dict:
    """
    Single LLM call per turn. If the LLM returns tool calls, the graph routes
    to the ToolNode. When it returns plain text, it sends to WhatsApp and ends.
    """
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        state.get("preferred_doctor", ""), "médico(a)"
    )
    patient_age = state.get("patient_age") or 99
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)
    duration_rule = (
        MINOR_RULE.format(
            patient_name=state.get("patient_name") or state.get("user_name", "paciente"),
            patient_age=patient_age,
        )
        if is_minor_first
        else ADULT_RULE
    )

    template = EXISTING_PATIENT_SYSTEM if state.get("is_patient") else NEW_PATIENT_SYSTEM
    today = datetime.now(ZoneInfo("America/Recife")).strftime("%d/%m/%Y %H:%M")
    system_prompt = template.format(
        patient_name=state.get("patient_name") or state.get("user_name", "paciente"),
        patient_age=patient_age,
        doctor=doctor_label,
        duration_rule=duration_rule,
        today=today,
    )

    messages = [SystemMessage(content=system_prompt), *state["messages"]]
    response = await _get_agent_llm().ainvoke(messages)

    # Only send to WhatsApp when the LLM produces a final text (no tool calls)
    if not response.tool_calls and response.content:
        await send_text(state["phone"], response.content)

    return {"messages": [response]}
