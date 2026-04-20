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
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor,
)
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, ADULT_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, CANCELLATION_RULES, CLINIC_ADDRESS, DOCTORS_INFO, BOOKING_FEE_RULE, MEDICAL_LIMITS_RULE, DOCTOR_CORRECTION_RULE, get_pricing_rules
from app.whatsapp import send_text
from app.database import upsert_user, log_event, get_upcoming_appointments, get_user_by_phone, DOCTOR_IDS, save_message

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor,
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

    # Detect receita request from any user message
    _messages_text = " ".join(
        m.content for m in state["messages"]
        if hasattr(m, "content") and isinstance(m.content, str)
    ).lower()
    _is_receita = "receita" in _messages_text

    # Detect if this is the very first bot response (no prior AIMessages)
    _has_greeted = any(getattr(m, "type", None) == "ai" for m in state["messages"])

    # Detect if the user has already made a specific request
    _request_keywords = [
        "receita", "agendar", "consulta", "laudo", "exame",
        "relatório", "relatorio", "nota fiscal", "declaração", "declaracao",
    ]
    _has_request = any(kw in _messages_text for kw in _request_keywords)

    async def _ask(reply: str) -> dict:
        await send_text(state["phone"], reply)
        await save_message(state["phone"], "assistant", reply)
        return {"messages": [AIMessage(content=reply)]}

    async def _extract_and_ask(extracted: dict, next_q: str) -> dict:
        """Save extracted fields and ask the next question in one turn."""
        await send_text(state["phone"], next_q)
        await save_message(state["phone"], "assistant", next_q)
        return {**extracted, "messages": [AIMessage(content=next_q)]}

    def _last_ai() -> str:
        for msg in reversed(state["messages"]):
            if getattr(msg, "type", None) == "ai":
                return msg.content or ""
        return ""

    def _last_human() -> str:
        for msg in reversed(state["messages"]):
            if getattr(msg, "type", None) == "human":
                return msg.content or ""
        return ""

    _extracted: dict = {}  # fields extracted programmatically this turn

    _NAME_Q = "Pode me informar o nome completo do paciente?"
    _CPF_Q = "Qual o CPF do paciente?"
    _BIRTH_Q = "Qual a data de nascimento do paciente? (formato dd/mm/aaaa)"
    _PATIENT_Q = "O paciente já é paciente da clínica?"
    _DOCTOR_Q = "Essa solicitação é para o Dr. Júlio ou para a Dra. Bruna?"
    _EMAIL_Q = "Qual o e-mail para envio?"
    _MED_Q = "Qual medicação você precisa na receita?"

    # Step 1: greeting + first question only when user already made a specific request
    if not _has_greeted and _has_request:
        greeting = (
            "Olá! 😊 Sou a Eva, assistente virtual da Clínica Psique.\n\n"
            "Claro, posso te ajudar com isso! Mas primeiro precisarei colher algumas informações.\n\n"
            + _NAME_Q
        )
        return await _ask(greeting)

    # Steps 2-8 only run when user has made a specific request
    if _has_request:
        from app.graph.schemas import _parse_birth_date
        last_ai = _last_ai()
        last_human = _last_human().strip()

        # Step 2: full name
        if not state.get("user_name"):
            if last_ai.endswith(_NAME_Q) and last_human:
                return await _extract_and_ask(
                    {"user_name": last_human, "patient_name": last_human}, _CPF_Q
                )
            return await _ask(_NAME_Q)

        # Step 3: CPF
        if not state.get("patient_cpf"):
            if last_ai == _CPF_Q and last_human:
                return await _extract_and_ask({"patient_cpf": last_human}, _BIRTH_Q)
            return await _ask(_CPF_Q)

        # Step 4: birth date
        if not state.get("birth_date"):
            if last_ai == _BIRTH_Q and last_human:
                parsed = _parse_birth_date(last_human)
                if parsed:
                    # Calculate age
                    bd = datetime.strptime(parsed, "%d/%m/%Y")
                    today = datetime.now()
                    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                    return await _extract_and_ask(
                        {"birth_date": parsed, "patient_age": age}, _PATIENT_Q
                    )
                else:
                    return await _ask("Não consegui identificar a data. Pode informar no formato dd/mm/aaaa? Ex: 15/01/1990.")
            return await _ask(_BIRTH_Q)

        # Step 5: is_patient
        if state.get("is_patient") is None:
            if last_ai == _PATIENT_Q and last_human:
                h = last_human.lower()
                if any(kw in h for kw in ["sim", "já", "ja", "sou", "é", "e paciente", "paciente"]):
                    is_patient = True
                elif any(kw in h for kw in ["não", "nao", "nunca", "primeira", "novo", "nova"]):
                    is_patient = False
                else:
                    is_patient = None
                if is_patient is not None:
                    return await _extract_and_ask({"is_patient": is_patient}, _DOCTOR_Q)
            return await _ask(_PATIENT_Q)

        # Step 6: preferred doctor
        if not state.get("preferred_doctor"):
            if last_ai == _DOCTOR_Q and last_human:
                h = last_human.lower()
                if "julio" in h or "júlio" in h:
                    doctor = "julio"
                elif "bruna" in h:
                    doctor = "bruna"
                else:
                    doctor = None
                if doctor:
                    return await _extract_and_ask({"preferred_doctor": doctor}, _EMAIL_Q)
            return await _ask(_DOCTOR_Q)

        # Step 7: email
        if not state.get("patient_email"):
            if last_ai == _EMAIL_Q and last_human:
                if _is_receita and not state.get("medication_note"):
                    return await _extract_and_ask({"patient_email": last_human}, _MED_Q)
                else:
                    # Last step — save and fall through to LLM to confirm
                    _extracted["patient_email"] = last_human
                    collected["patient_email"] = last_human
            else:
                return await _ask(_EMAIL_Q)

        # Step 8: medication — only for receita (last step)
        if _is_receita and not state.get("medication_note"):
            if last_ai == _MED_Q and last_human:
                # Last step — save and fall through to LLM to confirm
                _extracted["medication_note"] = last_human
                collected["medication_note"] = last_human
            else:
                return await _ask(_MED_Q)

        # All programmatic steps complete — _extracted will be merged into update below

    messages = [
        SystemMessage(content=COLLECT_SYSTEM.format(collected=collected, pricing_rules=get_pricing_rules(datetime.now()), medical_limits_rule=MEDICAL_LIMITS_RULE)),
        *state["messages"],
    ]

    result: CollectInfoOutput = await _get_collect_llm().ainvoke(messages)

    # Only show parse error if the LLM provided a non-empty string that failed validation.
    birth_date_invalid = (
        result.birth_date_parse_failed
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
        "user_name", "is_for_self", "patient_name",
        "birth_date", "patient_cpf", "guardian_relationship", "guardian_name", "guardian_cpf",
        "is_patient", "preferred_doctor", "patient_email",
        "consultation_reason", "referral_professional", "medication_note",
    ]:
        val = getattr(result, field, None)
        if val is not None:
            update[field] = val

    # Merge any fields extracted programmatically this turn
    for k, v in _extracted.items():
        update[k] = v

    # Calculate age automatically from birth_date
    birth_date_str = update.get("birth_date") or state.get("birth_date")
    if birth_date_str and not state.get("patient_age"):
        try:
            bd = datetime.strptime(birth_date_str, "%d/%m/%Y")
            today = datetime.now()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            update["patient_age"] = age
        except ValueError:
            pass

    if result.is_complete and not birth_date_invalid:
        update["stage"] = "patient_agent"

        # Merge collected fields with existing state for the upsert
        merged = {**state, **{k: v for k, v in update.items() if k not in ("messages", "stage")}}
        try:
            await upsert_user(state["phone"], {
                "name": merged.get("user_name"),
                "patient_name": merged.get("patient_name"),
                "age": merged.get("patient_age"),
                "birth_date": merged.get("birth_date"),
                "patient_cpf": merged.get("patient_cpf"),
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
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("Failed to upsert user after collect_info")

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
        patient_email=state.get("patient_email") or "não informado",
        doctor_correction_rule=DOCTOR_CORRECTION_RULE,
        booking_fee_rule=BOOKING_FEE_RULE,
        cancellation_rules=CANCELLATION_RULES,
        pricing_rules=get_pricing_rules(datetime.now()),
        clinic_address=CLINIC_ADDRESS,
        doctors_info=DOCTORS_INFO,
        medical_limits_rule=MEDICAL_LIMITS_RULE,
    )

    # One-time price adjustment notice injected into the system prompt (before May 2026)
    needs_price_notice = False
    now_dt = datetime.now(ZoneInfo("America/Recife"))
    if (now_dt.year, now_dt.month) < (2026, 5):
        user = await get_user_by_phone(state["phone"])
        if user and not user.get("price_adjustment_notified_at"):
            needs_price_notice = True
            system_prompt += (
                "\n\nAVISO ÚNICO OBRIGATÓRIO NESTA MENSAGEM: Inclua no início da sua resposta, "
                "de forma natural e acolhedora, que o valor da consulta deste paciente será "
                "reajustado em maio de 2026. Use a tabela abaixo para informar APENAS o novo "
                "valor correspondente ao médico e perfil deste paciente:\n"
                "  • Dra. Bruna → R$ 700,00 (hoje R$ 600,00)\n"
                "  • Dr. Júlio, adulto → R$ 700,00 (hoje R$ 600,00)\n"
                "  • Dr. Júlio, 1ª consulta infantil (< 18 anos) → R$ 850,00 (hoje R$ 750,00)\n"
                "  • Dr. Júlio, retorno infantil → R$ 750,00 (hoje R$ 650,00)\n"
                "Se for Dr. Júlio e ainda não souber se é primeira consulta ou retorno, "
                "pergunte antes de informar o valor. "
                "Faça isso independentemente do assunto da conversa."
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

    import logging as _log
    _logger = _log.getLogger(__name__)

    # Remove orphan tool_calls: AIMessages with tool_calls not followed by ToolMessages
    raw_messages = list(state["messages"])
    clean_messages = []
    for i, msg in enumerate(raw_messages):
        if getattr(msg, "tool_calls", None):
            next_msg = raw_messages[i + 1] if i + 1 < len(raw_messages) else None
            if next_msg is None or next_msg.type != "tool":
                continue  # skip orphan tool call
        clean_messages.append(msg)

    messages = [SystemMessage(content=system_prompt), *clean_messages]
    response = await _get_agent_llm().ainvoke(messages)

    _logger.info("AGENT_DEBUG tool_calls=%s content_len=%s",
                 [t["name"] for t in response.tool_calls] if response.tool_calls else [],
                 len(response.content) if response.content else 0)

    # Only send to WhatsApp when the LLM produces a final text (no tool calls)
    if not response.tool_calls and response.content:
        await send_text(state["phone"], response.content)
        await save_message(state["phone"], "assistant", response.content)
        if needs_price_notice:
            await upsert_user(state["phone"], {"price_adjustment_notified_at": now_dt.isoformat()})

    return {"messages": [response]}
