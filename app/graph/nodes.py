import os
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
    register_payment, update_preferred_doctor, save_patient_email,
    register_refund_request, confirm_refund_completed,
    request_registration_update,
    _expected_consultation_amount,
)
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, MINOR_RETURNING_RULE, ADULT_RULE, GUARDIAN_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, CANCELLATION_RULES, CLINIC_ADDRESS, DOCTORS_INFO, get_booking_fee_rule, MEDICAL_LIMITS_RULE, DOCTOR_CORRECTION_RULE, EMAIL_RULE, get_pricing_rules, ATTENDANT_INSTRUCTION_RULE, get_pricing_exception_rule
from app.whatsapp import send_text
from app.database import upsert_user, log_event, get_upcoming_appointments, get_user_by_phone, get_users_by_phone, DOCTOR_IDS, DOCTOR_NAMES, save_message, get_last_assistant_message_time
from app.chatwoot import get_conversation_id, add_private_note

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    register_refund_request, confirm_refund_completed,
    request_registration_update,
]

_collect_llm = None
_agent_llm = None


def _get_collect_llm():
    global _collect_llm
    if _collect_llm is None:
        _collect_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0).with_structured_output(CollectInfoOutput)
    return _collect_llm


def _get_agent_llm():
    global _agent_llm
    if _agent_llm is None:
        _agent_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0).bind_tools(TOOLS)
    return _agent_llm


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def collect_info_node(state: ConversationState, config: RunnableConfig) -> dict:
    collected = {
        "user_name": state.get("user_name"),
        "is_patient": state.get("is_patient"),
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

    # ── Multi-patient disambiguation ─────────────────────────────────────────────
    # Runs only when the phone has multiple registered patients and no patient has
    # been selected yet for this conversation.
    if not state.get("user_name"):
        pending = state.get("pending_patients")

        if pending is None:
            all_users = await get_users_by_phone(state["phone"])

            if len(all_users) > 1:
                # Auto-select the patient with a scheduled appointment — no disambiguation needed.
                from app.database import get_supabase as _get_supabase
                import datetime as _dt
                _client = await _get_supabase()
                _now = _dt.datetime.now(_dt.timezone.utc).isoformat()
                _user_ids = [u["id"] for u in all_users]
                _appt_result = await (
                    _client.from_("appointments")
                    .select("user_id")
                    .in_("user_id", _user_ids)
                    .eq("status", "scheduled")
                    .gte("start_time", _now)
                    .execute()
                )
                _scheduled_user_ids = {r["user_id"] for r in (_appt_result.data or [])}
                _with_appt = [u for u in all_users if u["id"] in _scheduled_user_ids]
                if len(_with_appt) == 1:
                    # Only one patient has a scheduled appointment — auto-select
                    all_users = _with_appt
                # else: multiple or none with appointments — show all for disambiguation

            if len(all_users) == 1:
                u = all_users[0]
                doc_key = DOCTOR_NAMES.get(u.get("doctor_id", ""), None)
                loaded = {
                    "user_db_id": u["id"],
                    "user_name": u.get("name"),
                    "patient_name": u.get("patient_name") or u.get("name"),
                    "patient_age": u.get("age"),
                    "birth_date": u.get("birth_date"),
                    "is_patient": u.get("is_patient"),
                    "is_returning_patient": u.get("is_returning_patient"),
                    "preferred_doctor": doc_key,
                    "patient_email": u.get("email"),
                    "guardian_name": u.get("guardian_name"),
                    "guardian_cpf": u.get("guardian_cpf"),
                    "guardian_relationship": u.get("guardian_relationship"),
                    "patient_cpf": u.get("patient_cpf"),
                    "modality_restriction": u.get("modality_restriction"),
                    "age_exception": u.get("age_exception"),
                }
                # Only skip collect_info if the patient already has an email.
                # If email is missing, stay in collect_info so Eva can ask for it.
                if loaded.get("patient_email"):
                    return {**loaded, "stage": "patient_agent", "messages": []}
                return loaded
            elif len(all_users) > 1:
                names = [u.get("patient_name") or u.get("name") or "Paciente" for u in all_users]
                options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
                reply = f"Olá! Para qual paciente você está entrando em contato?\n\n{options}"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {"pending_patients": all_users, "messages": [AIMessage(content=reply)]}

        elif state.get("pending_confirmation_patient"):
            candidate = state["pending_confirmation_patient"]
            last_human = ""
            for msg in reversed(state["messages"]):
                if getattr(msg, "type", None) == "human":
                    last_human = (msg.content or "").strip().lower()
                    break

            _affirmative = {"sim", "s", "yes", "y", "isso", "correto", "certo", "exato", "confirmado", "confirmo", "ok"}
            _negative    = {"não", "nao", "no", "n", "errado", "incorreto", "outro", "outra"}

            if any(w in last_human for w in _affirmative):
                doc_key = DOCTOR_NAMES.get(candidate.get("doctor_id", ""), None)
                return {
                    "pending_confirmation_patient": None,
                    "pending_patients": None,
                    "user_db_id": candidate["id"],
                    "user_name": candidate.get("name"),
                    "patient_name": candidate.get("patient_name") or candidate.get("name"),
                    "patient_age": candidate.get("age"),
                    "birth_date": candidate.get("birth_date"),
                    "is_patient": candidate.get("is_patient"),
                    "is_returning_patient": candidate.get("is_returning_patient"),
                    "preferred_doctor": doc_key,
                    "patient_email": candidate.get("email"),
                    "guardian_name": candidate.get("guardian_name"),
                    "guardian_cpf": candidate.get("guardian_cpf"),
                    "guardian_relationship": candidate.get("guardian_relationship"),
                    "patient_cpf": candidate.get("patient_cpf"),
                    "modality_restriction": candidate.get("modality_restriction"),
                    "age_exception": candidate.get("age_exception"),
                    "stage": "patient_agent",
                    "messages": [],
                }
            elif any(w in last_human for w in _negative):
                # Guardian rejected — re-show full list
                all_pending = state.get("pending_patients") or []
                names = [u.get("patient_name") or u.get("name") or "Paciente" for u in all_pending]
                options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
                reply = f"Sem problema! Para qual paciente você está entrando em contato?\n\n{options}"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {
                    "pending_confirmation_patient": None,
                    "pending_patients": all_pending,
                    "messages": [AIMessage(content=reply)],
                }
            else:
                # Ambiguous — ask again
                patient_name = candidate.get("patient_name") or candidate.get("name") or "o paciente"
                reply = f"Desculpe, não entendi. Você está entrando em contato para *{patient_name}*? (sim/não)"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {"messages": [AIMessage(content=reply)]}

        elif pending:
            last_human = ""
            for msg in reversed(state["messages"]):
                if getattr(msg, "type", None) == "human":
                    last_human = (msg.content or "").strip().lower()
                    break

            selected = None
            for i, u in enumerate(pending):
                name = (u.get("patient_name") or u.get("name") or "").lower()
                name_parts = name.split()
                if str(i + 1) == last_human or any(part in last_human for part in name_parts if len(part) > 2):
                    selected = u
                    break

            if selected:
                patient_name = selected.get("patient_name") or selected.get("name") or "o paciente"
                reply = f"Só confirmar: você está entrando em contato para *{patient_name}*, certo? (sim/não)"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {
                    "pending_confirmation_patient": selected,
                    "pending_patients": pending,
                    "messages": [AIMessage(content=reply)],
                }
            else:
                # Could not parse — ask again
                names = [u.get("patient_name") or u.get("name") or "Paciente" for u in pending]
                options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
                reply = f"Não consegui identificar. Pode digitar o número ou o nome do paciente?\n\n{options}"
                await send_text(state["phone"], reply)
                await save_message(state["phone"], "assistant", reply)
                return {"messages": [AIMessage(content=reply)]}

    # Detect receita request from any user message
    _messages_text = " ".join(
        m.content for m in state["messages"]
        if hasattr(m, "content") and isinstance(m.content, str)
    ).lower()
    _is_receita = "receita" in _messages_text

    # Detect document requests (email is only needed for these, not for scheduling)
    _doc_keywords = ["receita", "laudo", "nota fiscal", "declaração", "declaracao",
                     "relatório", "relatorio", "exame", "atestado"]
    _is_document = any(kw in _messages_text for kw in _doc_keywords)

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
        """Persist extracted fields to Supabase and ask the next question in one turn."""
        _STATE_TO_DB = {
            "user_name": "name",
            "patient_name": "patient_name",
            "patient_cpf": "patient_cpf",
            "birth_date": "birth_date",
            "patient_age": "age",
            "guardian_name": "guardian_name",
            "guardian_cpf": "guardian_cpf",
            "guardian_relationship": "guardian_relationship",
            "is_patient": "is_patient",
            "is_returning_patient": "is_returning_patient",
            "patient_email": "email",
        }
        db_payload = {_STATE_TO_DB[k]: v for k, v in extracted.items() if k in _STATE_TO_DB}
        if "preferred_doctor" in extracted:
            db_payload["doctor_id"] = DOCTOR_IDS.get(extracted["preferred_doctor"])
        result_update: dict = {**extracted, "messages": [AIMessage(content=next_q)]}
        if db_payload:
            try:
                returned_id = await upsert_user(state["phone"], db_payload, user_id=state.get("user_db_id"))
                if returned_id and not state.get("user_db_id"):
                    result_update["user_db_id"] = returned_id
            except Exception:
                import logging as _log
                _log.getLogger(__name__).exception("Failed to persist partial collect_info data")
        await send_text(state["phone"], next_q)
        await save_message(state["phone"], "assistant", next_q)
        return result_update

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
    _GUARDIAN_NAME_Q = "Qual é o nome completo do responsável pelo paciente?"
    _GUARDIAN_CPF_Q = "Qual é o CPF do responsável?"
    _PATIENT_Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    _CONTACT_NAME_Q = "Qual o seu nome completo para contato?"
    _DOCTOR_Q = "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?"
    _EMAIL_Q = "Qual o e-mail para envio?"
    _EMAIL_Q_CADASTRO = "Qual o seu e-mail? Precisamos para incluir no seu cadastro. 📋"
    _MED_Q = "Qual medicação você precisa na receita?"

    # Step 1: greeting + first MISSING question (skip fields already in state)
    if not _has_greeted and _has_request:
        _pat_age_for_greeting = state.get("patient_age") or 99
        if not state.get("user_name"):
            first_q = _NAME_Q
        elif not state.get("patient_cpf"):
            first_q = _CPF_Q
        elif not state.get("birth_date"):
            first_q = _BIRTH_Q
        elif _pat_age_for_greeting < 18 and not state.get("guardian_name"):
            first_q = _GUARDIAN_NAME_Q
        elif _pat_age_for_greeting < 18 and not state.get("guardian_cpf"):
            first_q = _GUARDIAN_CPF_Q
        elif _pat_age_for_greeting >= 18 and state.get("is_patient") is None:
            _pf = (state.get("patient_name") or state.get("user_name") or "").split()[0]
            first_q = f"Você é o(a) paciente {_pf} ou está agendando em nome dele(a)?"
        elif _pat_age_for_greeting >= 18 and state.get("is_patient") is False and state.get("user_name") == state.get("patient_name"):
            first_q = _CONTACT_NAME_Q
        elif state.get("is_returning_patient") is None:
            first_q = _PATIENT_Q
        elif not state.get("preferred_doctor"):
            first_q = _DOCTOR_Q
        elif not state.get("patient_email"):
            first_q = _EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO
        else:
            first_q = None  # all fields present — fall through to LLM
        if first_q:
            greeting = (
                "Olá! 😊 Sou a Eva, assistente virtual da Clínica Psique.\n\n"
                "Claro, posso te ajudar com isso! Mas primeiro precisarei colher algumas informações.\n\n"
                + first_q
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
            if last_ai and _CPF_Q in last_ai and last_human:
                import re as _re
                if _re.search(r'\d', last_human):
                    return await _extract_and_ask({"patient_cpf": last_human}, _BIRTH_Q)
                else:
                    return await _ask("CPF inválido. Por favor, informe o CPF do paciente com os números (ex: 123.456.789-10).")
            return await _ask(_CPF_Q)

        # Step 4: birth date
        if not state.get("birth_date"):
            # Use semantic match so any phrasing of the birth-date question is accepted
            asked_birth = "nascimento" in last_ai.lower()
            if asked_birth and last_human:
                parsed = _parse_birth_date(last_human)
                if parsed:
                    # Calculate age
                    bd = datetime.strptime(parsed, "%d/%m/%Y")
                    today = datetime.now()
                    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                    if age < 18:
                        # Minor: guardian is always the contact — mark is_patient=False and ask guardian name
                        return await _extract_and_ask(
                            {"birth_date": parsed, "patient_age": age, "is_patient": False},
                            _GUARDIAN_NAME_Q,
                        )
                    else:
                        # Adult: ask whether the contact is the patient themselves
                        _pf = (state.get("patient_name") or state.get("user_name") or "").split()[0]
                        _contact_q = f"Você é o(a) paciente {_pf} ou está agendando em nome dele(a)?"
                        return await _extract_and_ask(
                            {"birth_date": parsed, "patient_age": age},
                            _contact_q,
                        )
                else:
                    return await _ask("Não consegui identificar a data. Pode informar no formato dd/mm/aaaa? Ex: 15/01/1990.")
            return await _ask(_BIRTH_Q)

        # Step 4b: guardian name (only for minors)
        # Also update user_name — the guardian IS the contact on WhatsApp.
        if (state.get("patient_age") or 99) < 18 and not state.get("guardian_name"):
            _last_ai_asked_guardian_name = last_ai and (
                _GUARDIAN_NAME_Q in last_ai
                or "responsável" in last_ai.lower()
                or "nome completo do" in last_ai.lower()
            )
            if _last_ai_asked_guardian_name and last_human:
                return await _extract_and_ask(
                    {"guardian_name": last_human, "user_name": last_human}, _GUARDIAN_CPF_Q
                )
            return await _ask(_GUARDIAN_NAME_Q)

        # Step 4c: guardian CPF (only for minors)
        if (state.get("patient_age") or 99) < 18 and not state.get("guardian_cpf"):
            _last_ai_asked_guardian_cpf = last_ai and (
                _GUARDIAN_CPF_Q in last_ai
                or ("cpf" in last_ai.lower() and "responsável" in last_ai.lower())
                or ("cpf" in last_ai.lower() and (state.get("guardian_name") or "").split()[0].lower() in last_ai.lower())
            )
            if _last_ai_asked_guardian_cpf and last_human:
                return await _extract_and_ask({"guardian_cpf": last_human}, _PATIENT_Q)
            return await _ask(_GUARDIAN_CPF_Q)

        # Step 4d: for adults, determine whether the contact is the patient themselves.
        # Uses is_patient (True = contact IS the patient; False = scheduling for someone else).
        if (state.get("patient_age") or 99) >= 18 and state.get("is_patient") is None:
            _asked_contact = (
                "agendando em nome" in last_ai.lower()
                or ("você é" in last_ai.lower() and "paciente" in last_ai.lower())
            )
            if _asked_contact and last_human:
                h = last_human.lower()
                _not_patient_kws = [
                    "não", "nao", "mãe", "mae", "pai", "filho", "filha",
                    "em nome", "para meu", "para minha", "esposo", "esposa",
                    "marido", "irmão", "irmao", "irma",
                ]
                is_patient = not any(kw in h for kw in _not_patient_kws)
                if is_patient:
                    return await _extract_and_ask({"is_patient": True}, _PATIENT_Q)
                else:
                    return await _extract_and_ask({"is_patient": False}, _CONTACT_NAME_Q)
            _pf = (state.get("patient_name") or state.get("user_name") or "").split()[0]
            return await _ask(f"Você é o(a) paciente {_pf} ou está agendando em nome dele(a)?")

        # Step 4e: contact name when scheduling for someone else (adults only).
        # user_name was initially set to patient_name in Step 2; overwrite with actual contact name.
        if (
            (state.get("patient_age") or 99) >= 18
            and state.get("is_patient") is False
            and state.get("user_name") == state.get("patient_name")
        ):
            if last_ai and _CONTACT_NAME_Q in last_ai and last_human:
                return await _extract_and_ask({"user_name": last_human}, _PATIENT_Q)
            return await _ask(_CONTACT_NAME_Q)

        # Step 5: is_returning_patient
        # "O paciente já é paciente da clínica?" → True = returning patient, False = new patient
        # is_patient (contact IS the patient vs scheduling for someone else) is a separate concept
        # and must NOT be set here.
        if state.get("is_returning_patient") is None:
            if last_ai and _PATIENT_Q in last_ai and last_human:
                h = last_human.lower()
                if any(kw in h for kw in ["sim", "já", "ja", "sou", "é", "e paciente", "paciente"]):
                    is_returning_patient = True
                elif any(kw in h for kw in ["não", "nao", "nunca", "primeira", "novo", "nova"]):
                    is_returning_patient = False
                else:
                    is_returning_patient = None
                if is_returning_patient is not None:
                    return await _extract_and_ask(
                        {"is_returning_patient": is_returning_patient}, _DOCTOR_Q
                    )
            return await _ask(_PATIENT_Q)

        # Step 6: preferred doctor
        if not state.get("preferred_doctor"):
            _last_ai_asked_doctor = last_ai and (
                _DOCTOR_Q in last_ai
                or "júlio" in last_ai.lower()
                or "julio" in last_ai.lower()
                or "bruna" in last_ai.lower()
                or "médico" in last_ai.lower()
                or "preferência" in last_ai.lower()
            )
            if _last_ai_asked_doctor and last_human:
                h = last_human.lower()
                if "julio" in h or "júlio" in h:
                    doctor = "julio"
                elif "bruna" in h:
                    doctor = "bruna"
                else:
                    doctor = None
                if doctor:
                    next_email_q = _EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO
                    return await _extract_and_ask({"preferred_doctor": doctor}, next_email_q)
            return await _ask(_DOCTOR_Q)

        # Step 7: email — ALWAYS required, whether scheduling or requesting a document
        if not state.get("patient_email"):
            _last_ai_asked_email = last_ai and (
                _EMAIL_Q in last_ai
                or _EMAIL_Q_CADASTRO in last_ai
                or "e-mail" in last_ai.lower()
                or "email" in last_ai.lower()
            )
            if _last_ai_asked_email and last_human and "@" in last_human:
                if _is_receita and not state.get("medication_note"):
                    return await _extract_and_ask({"patient_email": last_human}, _MED_Q)
                else:
                    # Last step — save and fall through to LLM to confirm
                    _extracted["patient_email"] = last_human
                    collected["patient_email"] = last_human
            elif _last_ai_asked_email and last_human and "@" not in last_human:
                return await _ask("E-mail inválido. Por favor, informe um e-mail válido (ex: nome@email.com).")
            else:
                return await _ask(_EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO)

        # Step 8: medication — only for receita (last step)
        if _is_receita and not state.get("medication_note"):
            if last_ai and _MED_Q in last_ai and last_human:
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
        "user_name", "is_patient", "patient_name",
        "birth_date", "patient_cpf", "guardian_relationship", "guardian_name", "guardian_cpf",
        "is_returning_patient", "preferred_doctor", "patient_email",
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
        if _is_document:
            update["pending_action"] = "request_document"

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
                "active": True,
            }, user_id=state.get("user_db_id"))
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
    _raw_age = state.get("patient_age")
    patient_age = _raw_age or 99          # numeric fallback for logic checks
    patient_age_display = f"{patient_age} anos" if _raw_age else "não informada"
    _full_name = state.get("patient_name") or state.get("user_name") or "paciente"
    first_name = _full_name.split()[0]
    # contact_name: who is on WhatsApp. May differ from patient_name (e.g. guardian).
    # When is_patient is explicitly False the contact is NOT the patient; avoid using
    # patient_name as fallback so the LLM doesn't confuse the two people.
    _is_third_party = state.get("is_patient") is False
    _contact_full = state.get("user_name") or (
        "responsável" if _is_third_party else (state.get("patient_name") or "paciente")
    )
    contact_first_name = _contact_full.split()[0]
    contact_name = _contact_full
    is_minor = patient_age < 18
    is_minor_first = (
        is_minor
        and not state.get("is_returning_patient", False)
        and state.get("preferred_doctor") == "julio"
    )
    if is_minor_first:
        duration_rule = MINOR_RULE.format(patient_name=first_name, patient_age=patient_age)
    elif is_minor:
        duration_rule = MINOR_RETURNING_RULE.format(patient_age=patient_age)
    else:
        duration_rule = ADULT_RULE

    # ── Auto-extract birth_date from recent messages if still missing ─────────
    # Handles the case where the LLM asked for birth_date but the node never
    # saved the patient's answer to state (patient_agent_node is stateless by default).
    birth_date = state.get("birth_date")
    if not birth_date:
        from app.graph.schemas import _parse_birth_date as _pbd
        msgs = list(state["messages"])
        for i in range(len(msgs) - 1, 0, -1):
            m = msgs[i]
            if m.type != "human" or not isinstance(m.content, str):
                continue
            parsed_bd = _pbd(m.content.strip())
            if not parsed_bd:
                break  # last human message is not a date — stop looking
            # Find the preceding AI message and check it asked about birth date
            prev_ai = next(
                (msgs[j] for j in range(i - 1, max(i - 5, -1), -1) if msgs[j].type == "ai"),
                None,
            )
            if prev_ai and "nascimento" in (prev_ai.content or "").lower():
                bd = datetime.strptime(parsed_bd, "%d/%m/%Y")
                today_dt = datetime.now()
                _age = today_dt.year - bd.year - ((today_dt.month, today_dt.day) < (bd.month, bd.day))
                await upsert_user(state["phone"], {"birth_date": parsed_bd, "age": _age}, user_id=state.get("user_db_id"))
                birth_date = parsed_bd
                patient_age = _age
            break

    from app.google_calendar import format_doctor_schedules
    template = EXISTING_PATIENT_SYSTEM if state.get("is_returning_patient") else NEW_PATIENT_SYSTEM
    _now_recife = datetime.now(ZoneInfo("America/Recife"))
    _weekday_pt = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"][_now_recife.weekday()]
    today = f"{_now_recife.strftime('%d/%m/%Y %H:%M')} ({_weekday_pt})"
    system_prompt = template.format(
        patient_name=first_name,
        contact_name=contact_name,
        patient_age=patient_age_display,
        birth_date=birth_date or "não informada",
        doctor=doctor_label,
        duration_rule=duration_rule,
        today=today,
        doctor_schedules=format_doctor_schedules(),
        patient_email=state.get("patient_email") or "não informado",
        email_rule=EMAIL_RULE,
        doctor_correction_rule=DOCTOR_CORRECTION_RULE,
        booking_fee_rule=get_booking_fee_rule(),
        cancellation_rules=CANCELLATION_RULES,
        pricing_rules=get_pricing_rules(datetime.now()),
        clinic_address=CLINIC_ADDRESS,
        doctors_info=DOCTORS_INFO,
        medical_limits_rule=MEDICAL_LIMITS_RULE,
        attendant_instruction_rule=ATTENDANT_INSTRUCTION_RULE,
        modality_restriction=state.get("modality_restriction") or "",
    )

    # Inject guardian context for minor patients
    if is_minor:
        system_prompt += GUARDIAN_RULE.format(
            patient_name=first_name,
            guardian_name=state.get("guardian_name") or "não informado",
            guardian_relationship=state.get("guardian_relationship") or "responsável",
            guardian_cpf=state.get("guardian_cpf") or "não informado",
        )

    # Attendant-mode: add patient_name_override reminder (routing rules are in the base prompt)
    if state.get("silent_mode"):
        system_prompt += (
            "\n\nLEMBRETE (modo atendente): Se a instrução mencionar um nome de paciente diferente "
            "do que está no contexto da conversa, passe-o em patient_name_override ao chamar "
            "confirm_appointment para garantir que o agendamento fique no nome correto."
        )

    # One-time price adjustment notice injected into the system prompt
    needs_price_notice = False
    now_dt = datetime.now(ZoneInfo("America/Recife"))
    user = await get_user_by_phone(state["phone"])
    _custom_price = (user or {}).get("custom_price")
    _fee_waived = bool((user or {}).get("booking_fee_waived", False))
    _is_exception_patient = _custom_price is not None or _fee_waived
    if user and not user.get("price_adjustment_notified_at") and not _is_exception_patient:
        needs_price_notice = True
        if (now_dt.year, now_dt.month) < (2026, 6):
            system_prompt += (
                "\n\nAVISO ÚNICO OBRIGATÓRIO NESTA MENSAGEM: Inclua no início da sua resposta, "
                "de forma natural e acolhedora, que o valor da consulta deste paciente será "
                "reajustado a partir de junho de 2026. Consultas até maio ainda têm o valor atual. "
                "Use a tabela abaixo para informar APENAS os valores correspondentes ao médico "
                "e perfil deste paciente:\n"
                "  • Dra. Bruna → até maio: R$ 600,00 / a partir de junho: R$ 700,00\n"
                "  • Dr. Júlio, adulto → até maio: R$ 600,00 / a partir de junho: R$ 700,00\n"
                "  • Dr. Júlio, 1ª consulta infantil (< 18 anos) → até maio: R$ 750,00 / a partir de junho: R$ 850,00\n"
                "  • Dr. Júlio, retorno infantil → até maio: R$ 650,00 / a partir de junho: R$ 750,00\n"
                "Se for Dr. Júlio e ainda não souber se é primeira consulta ou acompanhamento,"
                "pergunte antes de informar o valor. "
                "Faça isso independentemente do assunto da conversa."
            )
        else:
            system_prompt += (
                "\n\nAVISO ÚNICO OBRIGATÓRIO NESTA MENSAGEM: Inclua no início da sua resposta, "
                "de forma natural e acolhedora, que os valores das consultas foram atualizados "
                "em junho de 2026. Não mencione os valores anteriores. "
                "Faça isso independentemente do assunto da conversa."
            )

    # ── Per-patient pricing exception block ──────────────────────────────────
    if _is_exception_patient:
        _doctor_key = state.get("preferred_doctor") or ""
        _p_age = state.get("patient_age") or 99
        _standard_price = _expected_consultation_amount(_doctor_key, _p_age, None, now_dt)
        system_prompt += get_pricing_exception_rule(_custom_price, _fee_waived, _standard_price)

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

    # Trim history to cap per-request token usage and avoid TPM rate limits.
    # Keep the most recent MAX_HISTORY_MESSAGES messages; always start on a
    # human turn so the LLM never sees an orphan tool-response as first message.
    _max_hist = int(os.getenv("MAX_HISTORY_MESSAGES", "30"))
    if len(clean_messages) > _max_hist:
        clean_messages = clean_messages[-_max_hist:]
        # Walk forward until the first message is a human message
        while clean_messages and getattr(clean_messages[0], "type", "") != "human":
            clean_messages = clean_messages[1:]

    # Detect whether this is the start of a new session:
    # (a) no prior AI messages at all — patient_agent_node seeing this patient for the first time, or
    # (b) there are prior AI messages but the last assistant reply was on a different calendar day.
    _has_prior_ai = any(getattr(m, "type", None) == "ai" for m in clean_messages)
    _is_new_session = not _has_prior_ai
    if _has_prior_ai:
        _last_ai_time = await get_last_assistant_message_time(state["phone"])
        if _last_ai_time is not None:
            _tz_recife = ZoneInfo("America/Recife")
            _is_new_session = _last_ai_time.astimezone(_tz_recife).date() < _now_recife.date()

    if _is_new_session:
        _hour = _now_recife.hour
        _greeting_word = "Bom dia" if _hour < 12 else ("Boa tarde" if _hour < 18 else "Boa noite")
        _session_label = "com o responsável/contato" if _is_third_party else "com o paciente"
        system_prompt += (
            f"\n\nINÍCIO DE CONVERSA: Esta é a primeira mensagem desta sessão {_session_label}. "
            f"Você DEVE começar sua resposta com '{_greeting_word}, {contact_first_name}! 😊' e se apresentar "
            f"como Eva da Clínica Psique antes de responder à solicitação."
        )

    # When the WhatsApp contact is NOT the patient, explicitly remind the LLM so it
    # doesn't address the contact as "paciente" or use "seu" for the patient's data.
    if _is_third_party:
        system_prompt += (
            f"\n\nAVISO IMPORTANTE: O contato no WhatsApp ({contact_name}) NÃO é o paciente. "
            f"Está agendando em nome do(a) paciente {first_name}. "
            f"Dirija-se ao contato pelo nome ({contact_first_name}), NUNCA como 'paciente'. "
            f"Ao pedir data de nascimento ou e-mail, deixe claro que são dados do(a) paciente {first_name} "
            f"(ex: 'Qual a data de nascimento de {first_name}?', 'Qual o e-mail de {first_name}?')."
        )

    # When we just transitioned from collect_info in the same turn, tell the agent
    # exactly what the user was trying to do so it doesn't get distracted by
    # unrelated context such as upcoming appointments.
    pending_action = state.get("pending_action")
    if pending_action == "request_document":
        system_prompt += (
            "\n\nAÇÃO IMEDIATA: O cadastro foi concluído agora mesmo nesta mesma mensagem. "
            "O usuário havia solicitado um documento (nota fiscal, laudo, receita, etc.). "
            "Chame request_document agora para processar essa solicitação. "
            "NÃO mencione consultas agendadas nem inicie outro assunto."
        )

    messages = [SystemMessage(content=system_prompt), *clean_messages]
    response = await _get_agent_llm().ainvoke(messages)

    _logger.info("AGENT_DEBUG tool_calls=%s content_len=%s",
                 [t["name"] for t in response.tool_calls] if response.tool_calls else [],
                 len(response.content) if response.content else 0)

    # Only send to WhatsApp when the LLM produces a final text (no tool calls)
    if not response.tool_calls and response.content:
        phone = state["phone"]

        # Detect internal-note responses by content prefix ONLY.
        # Do NOT use silent_mode here: when an attendant sends a command, Eva must
        # still deliver patient-facing messages to the patient via WhatsApp.
        # silent_mode only controls the system-prompt instruction (see below).
        _INTERNAL_PREFIXES = (
            "nota para a equipe",
            "nota interna",
            "nota para equipe",
            "[nota interna]",
            "[nota para a equipe]",
        )
        _content_lower = response.content.lstrip().lower()
        is_internal = any(_content_lower.startswith(p) for p in _INTERNAL_PREFIXES)

        if is_internal:
            # Post as Chatwoot private note.
            # Fallback to WhatsApp if conv_id is missing or the API call fails —
            # better the message arrive in the wrong place than disappear silently.
            import logging as _log
            _node_logger = _log.getLogger(__name__)
            conv_id = get_conversation_id(phone)
            _node_logger.info("PRIVATE_NOTE attempt phone=%s conv_id=%s", phone, conv_id)
            posted = False
            if conv_id:
                try:
                    await add_private_note(conv_id, response.content)
                    posted = True
                except Exception:
                    _node_logger.exception(
                        "PRIVATE_NOTE FAILED phone=%s conv_id=%s — falling back to WhatsApp", phone, conv_id
                    )
            if not posted:
                _node_logger.warning(
                    "PRIVATE_NOTE no conv_id or post failed — sending to WhatsApp phone=%s", phone
                )
                await send_text(phone, response.content)
            await save_message(phone, "assistant", response.content)
        else:
            await send_text(phone, response.content)
            await save_message(phone, "assistant", response.content)
            if needs_price_notice:
                await upsert_user(phone, {"price_adjustment_notified_at": now_dt.isoformat()}, user_id=state.get("user_db_id"))

    update: dict = {"messages": [response]}
    if pending_action:
        update["pending_action"] = None

    # If preferred_doctor is missing from state, check whether update_preferred_doctor
    # was just called (state may not reflect it yet because ToolNode only updates messages).
    if not state.get("preferred_doctor"):
        all_msgs = list(state["messages"]) + [response]
        for msg in reversed(all_msgs):
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if tc.get("name") == "update_preferred_doctor":
                    doctor_val = (tc.get("args") or {}).get("doctor")
                    if doctor_val:
                        update["preferred_doctor"] = doctor_val
                        if state.get("user_db_id"):
                            from app.database import DOCTOR_IDS as _DIDS
                            try:
                                await upsert_user(state["phone"], {"doctor_id": _DIDS.get(doctor_val)}, user_id=state.get("user_db_id"))
                            except Exception:
                                _logger.exception("Failed to persist preferred_doctor after update_preferred_doctor tool call")
                        break
            if "preferred_doctor" in update:
                break

    return update
