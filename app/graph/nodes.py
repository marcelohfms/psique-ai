from datetime import datetime
from zoneinfo import ZoneInfo
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import ConversationState
from app.graph.schemas import CollectInfoOutput
from app.graph.tools import (
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment,
    request_document, transfer_to_human,
)
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, ADULT_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, PRICING_RULES, CANCELLATION_RULES, CLINIC_ADDRESS
from app.uazapi import send_text
from app.database import upsert_user, log_event, get_upcoming_appointments, DOCTOR_IDS, save_message

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment,
    request_document, transfer_to_human,
]

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
        "birth_date": state.get("birth_date"),
        "guardian_relationship": state.get("guardian_relationship"),
        "guardian_name": state.get("guardian_name"),
        "guardian_cpf": state.get("guardian_cpf"),
        "is_patient": state.get("is_patient"),
        "preferred_doctor": state.get("preferred_doctor"),
        "patient_email": state.get("patient_email"),
        "consultation_reason": state.get("consultation_reason"),
        "referral_professional": state.get("referral_professional"),
    }

    messages = [
        SystemMessage(content=COLLECT_SYSTEM.format(collected=collected, pricing_rules=PRICING_RULES)),
        *state["messages"],
    ]

    result: CollectInfoOutput = await _get_collect_llm().ainvoke(messages)

    # Detect when the LLM tried to set birth_date but the validator rejected the format.
    # model_fields_set contains fields that were explicitly provided by the LLM,
    # so if birth_date is in it but the validated result is None, the format was wrong.
    birth_date_invalid = (
        "birth_date" in result.model_fields_set
        and result.birth_date is None
        and state.get("birth_date") is None
    )

    reply = result.reply
    if birth_date_invalid:
        reply = (
            "Não consegui identificar a data de nascimento no formato correto. "
            "Poderia informar no formato dd/mm/aaaa? Por exemplo: 15/01/1994."
        )

    await send_text(state["phone"], reply)
    await save_message(state["phone"], "assistant", reply)

    update: dict = {"messages": [AIMessage(content=reply)]}

    for field in [
        "user_name", "is_for_self", "patient_name", "patient_age",
        "birth_date", "guardian_relationship", "guardian_name", "guardian_cpf",
        "is_patient", "preferred_doctor", "patient_email",
        "consultation_reason", "referral_professional",
    ]:
        val = getattr(result, field)
        if val is not None:
            update[field] = val

    if result.is_complete and not birth_date_invalid:
        update["stage"] = "patient_agent"

        # Merge collected fields with existing state for the upsert
        merged = {**state, **{k: v for k, v in update.items() if k not in ("messages", "stage")}}
        await upsert_user(state["phone"], {
            "name": merged.get("user_name"),
            "patient_name": merged.get("patient_name"),
            "age": merged.get("patient_age"),
            "birth_date": merged.get("birth_date"),
            "guardian_name": merged.get("guardian_name"),
            "guardian_cpf": merged.get("guardian_cpf"),
            "guardian_relationship": merged.get("guardian_relationship"),
            "is_patient": merged.get("is_patient"),
            "doctor_id": DOCTOR_IDS.get(merged.get("preferred_doctor", ""), None),
            "email": merged.get("patient_email"),
            "consultation_reason": merged.get("consultation_reason"),
            "referral_professional": merged.get("referral_professional"),
        })
        await log_event("info_collected", state["phone"], {
            "patient_name": merged.get("patient_name"),
            "patient_age": merged.get("patient_age"),
            "is_patient": merged.get("is_patient"),
            "preferred_doctor": merged.get("preferred_doctor"),
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
    _full_name = state.get("patient_name") or state.get("user_name") or "paciente"
    first_name = _full_name.split()[0]
    is_minor_first = (
        patient_age < 18
        and not state.get("is_patient", False)
        and state.get("preferred_doctor") == "julio"
    )
    duration_rule = (
        MINOR_RULE.format(
            patient_name=first_name,
            patient_age=patient_age,
        )
        if is_minor_first
        else ADULT_RULE
    )

    from app.google_calendar import format_doctor_schedules
    template = EXISTING_PATIENT_SYSTEM if state.get("is_patient") else NEW_PATIENT_SYSTEM
    today = datetime.now(ZoneInfo("America/Recife")).strftime("%d/%m/%Y %H:%M")
    system_prompt = template.format(
        patient_name=first_name,
        patient_age=patient_age,
        doctor=doctor_label,
        duration_rule=duration_rule,
        today=today,
        doctor_schedules=format_doctor_schedules(),
        cancellation_rules=CANCELLATION_RULES,
        pricing_rules=PRICING_RULES,
        clinic_address=CLINIC_ADDRESS,
    )

    # Inject upcoming appointments so the LLM knows what already exists
    upcoming = await get_upcoming_appointments(state["phone"])
    if upcoming:
        from zoneinfo import ZoneInfo as _ZI
        _TZ = _ZI("America/Recife")
        lines = ["Consultas agendadas para este paciente:"]
        for apt in upcoming:
            dt = datetime.fromisoformat(apt["start_time"]).astimezone(_TZ)
            lines.append(f"- {dt.strftime('%d/%m/%Y às %H:%M')} (ID: {apt['appointment_id']})")
        system_prompt += "\n\n" + "\n".join(lines)

    messages = [SystemMessage(content=system_prompt), *state["messages"]]
    response = await _get_agent_llm().ainvoke(messages)

    # Only send to WhatsApp when the LLM produces a final text (no tool calls)
    if not response.tool_calls and response.content:
        await send_text(state["phone"], response.content)
        await save_message(state["phone"], "assistant", response.content)

    return {"messages": [response]}
