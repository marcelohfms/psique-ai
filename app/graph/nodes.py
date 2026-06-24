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
    cancel_appointment, reschedule_appointment, mark_reschedule_in_progress,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data,
    _expected_consultation_amount,
)
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, MINOR_RETURNING_RULE, ADULT_RULE, GUARDIAN_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, CANCELLATION_RULES, CLINIC_ADDRESS, DOCTORS_INFO, get_booking_fee_rule, MEDICAL_LIMITS_RULE, DOCTOR_CORRECTION_RULE, EMAIL_RULE, get_pricing_rules, ATTENDANT_INSTRUCTION_RULE, get_pricing_exception_rule
from app.whatsapp import send_text
from app.database import upsert_user, log_event, get_upcoming_appointments, get_user_by_phone, get_users_by_phone, DOCTOR_IDS, DOCTOR_NAMES, save_message, get_last_assistant_message_time, is_registration_complete
from app.chatwoot import get_conversation_id, add_private_note

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, reschedule_appointment, mark_reschedule_in_progress,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data,
]

_collect_llm = None
_agent_llm = None


def _get_collect_llm():
    global _collect_llm
    if _collect_llm is None:
        _collect_llm = ChatOpenAI(model="gpt-4.1", temperature=0).with_structured_output(CollectInfoOutput)
    return _collect_llm


def _get_agent_llm():
    global _agent_llm
    if _agent_llm is None:
        _agent_llm = ChatOpenAI(model="gpt-4.1", temperature=0).bind_tools(TOOLS)
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
                    .select("patient_id")
                    .in_("patient_id", _user_ids)
                    .eq("status", "scheduled")
                    .gte("start_time", _now)
                    .execute()
                )
                _scheduled_user_ids = {r["patient_id"] for r in (_appt_result.data or [])}
                _with_appt = [u for u in all_users if u["id"] in _scheduled_user_ids]
                if len(_with_appt) == 1:
                    # Only one patient has a scheduled appointment — auto-select
                    all_users = _with_appt
                # else: multiple or none with appointments — show all for disambiguation

            if len(all_users) == 1:
                u = all_users[0]

                # If the existing record is for someone else (is_patient=False) and the
                # contact is now booking for themselves ("para mim"), start a fresh
                # registration instead of loading the third-party record. This creates
                # a new user entry linked to this phone number with is_patient=True.
                _self_keywords = [
                    "para mim", "pra mim", "para eu", "sou eu", "sou a paciente",
                    "sou o paciente", "é para mim", "e para mim", "é pra mim",
                    "e pra mim", "para mim mesmo", "pra mim mesmo",
                ]
                _last_human_msg = ""
                for _m in reversed(state.get("messages", [])):
                    if getattr(_m, "type", "") == "human":
                        _last_human_msg = (getattr(_m, "content", "") or "").lower()
                        break
                _contact_booking_for_self = (
                    u.get("is_patient") is False
                    and any(kw in _last_human_msg for kw in _self_keywords)
                )
                if _contact_booking_for_self:
                    # Don't load the existing third-party record — let collect_info
                    # gather fresh data and create a new user with is_patient=True.
                    # Pre-fill only the contact name so Eva doesn't ask again.
                    pass  # fall through to normal collect_info flow below

                else:
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
                    # Only skip collect_info when ALL required fields are present.
                    if is_registration_complete(u):
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

    # Fields that must be merged into EVERY return of this turn (so a doctor
    # mentioned mid-conversation is never lost on subsequent early returns).
    _persistent_updates: dict = {}

    # Detect a doctor preference stated anywhere in the conversation and persist it
    # IMMEDIATELY to the cadastro — e.g. "quero agendar com a Dra. Bruna".
    # Only acts when exactly one doctor is mentioned (avoids ambiguity).
    if not state.get("preferred_doctor"):
        _mentions_bruna = "bruna" in _messages_text
        _mentions_julio = "júlio" in _messages_text or "julio" in _messages_text
        _doc_key = None
        if _mentions_bruna and not _mentions_julio:
            _doc_key = "bruna"
        elif _mentions_julio and not _mentions_bruna:
            _doc_key = "julio"
        if _doc_key:
            state["preferred_doctor"] = _doc_key
            _persistent_updates["preferred_doctor"] = _doc_key
            try:
                returned_id = await upsert_user(
                    state["phone"],
                    {"doctor_id": DOCTOR_IDS.get(_doc_key)},
                    user_id=state.get("user_db_id"),
                )
                if returned_id and not state.get("user_db_id"):
                    state["user_db_id"] = returned_id
                    _persistent_updates["user_db_id"] = returned_id
            except Exception:
                import logging as _log
                _log.getLogger(__name__).exception("Failed to persist preferred_doctor")

    async def _ask(reply: str) -> dict:
        await send_text(state["phone"], reply)
        await save_message(state["phone"], "assistant", reply)
        return {**_persistent_updates, "messages": [AIMessage(content=reply)]}

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
        result_update: dict = {**_persistent_updates, **extracted, "messages": [AIMessage(content=next_q)]}
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

    _collect_system = COLLECT_SYSTEM.format(
        collected=collected,
        pricing_rules=get_pricing_rules(datetime.now()),
        medical_limits_rule=MEDICAL_LIMITS_RULE,
    )
    # Inject contact-vs-patient rule when already known during collect phase
    _ci_is_third_party = state.get("is_patient") is False
    _ci_user_name = state.get("user_name")
    _ci_patient_name = state.get("patient_name")
    if _ci_is_third_party and _ci_user_name and _ci_patient_name and _ci_user_name != _ci_patient_name:
        _ci_contact_first = _ci_user_name.split()[0]
        _ci_patient_first = _ci_patient_name.split()[0]
        _collect_system += (
            f"\n\nIMPORTANTE — CONTATO ≠ PACIENTE: Quem está no WhatsApp é *{_ci_user_name}* "
            f"(o contato/responsável), NÃO o paciente *{_ci_patient_first}*. "
            f"Em TODAS as mensagens, dirija-se a {_ci_contact_first} pelo nome. "
            f"Use o nome {_ci_patient_first} apenas quando falar SOBRE o paciente na terceira pessoa."
        )
    messages = [
        SystemMessage(content=_collect_system),
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
        "user_name", "patient_name",
        "birth_date", "patient_cpf", "guardian_relationship", "guardian_name", "guardian_cpf",
        "is_returning_patient", "preferred_doctor", "patient_email",
        "consultation_reason", "referral_professional", "medication_note",
    ]:
        val = getattr(result, field, None)
        if val is not None:
            update[field] = val

    # is_patient must ONLY be set via programmatic steps (Step 4 for minors, Step 4d for adults).
    # The LLM must not infer it from context — this avoids silently setting is_patient=False
    # without asking the "Você é o(a) paciente?" question.
    # Only apply the LLM-extracted value if the programmatic steps haven't set it yet AND the
    # last AI message actually asked about it (meaning the programmatic step 4d ran and the
    # user answered, but somehow the extraction loop above is handling it).
    if result.is_patient is not None and state.get("is_patient") is None:
        _last_ai_text = ""
        for _m in reversed(state.get("messages", [])):
            if getattr(_m, "type", None) == "ai":
                _last_ai_text = (_m.content or "").lower()
                break
        _asked_is_patient = (
            "agendando em nome" in _last_ai_text
            or ("você é" in _last_ai_text and "paciente" in _last_ai_text)
        )
        if _asked_is_patient:
            update["is_patient"] = result.is_patient

    # Merge any fields extracted programmatically this turn
    for k, v in _extracted.items():
        update[k] = v

    # If the LLM just extracted preferred_doctor for the first time, persist it to DB
    # immediately so it's not lost if is_complete=True is delayed or the conversation resets.
    if (
        update.get("preferred_doctor")
        and not state.get("preferred_doctor")
        and state.get("user_db_id")
    ):
        try:
            await upsert_user(state["phone"], {
                "doctor_id": DOCTOR_IDS.get(update["preferred_doctor"])
            }, user_id=state["user_db_id"])
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("Failed to persist preferred_doctor early")

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

    # Merge collected fields with existing state for the upsert (used both for
    # is_complete check and for the actual upsert below).
    merged = {**state, **{k: v for k, v in update.items() if k not in ("messages", "stage")}}

    # Map merged state to the dict shape expected by is_registration_complete.
    _reg_check = {
        "name": merged.get("user_name"),
        "email": merged.get("patient_email"),
        "birth_date": merged.get("birth_date"),
        "doctor_id": DOCTOR_IDS.get(merged.get("preferred_doctor", ""), None),
        "is_patient": merged.get("is_patient"),
        "is_returning_patient": merged.get("is_returning_patient"),
        "patient_name": merged.get("patient_name"),
        "age": merged.get("patient_age"),
        "guardian_name": merged.get("guardian_name"),
        "guardian_cpf": merged.get("guardian_cpf"),
        "guardian_relationship": merged.get("guardian_relationship"),
    }

    if result.is_complete and not birth_date_invalid and is_registration_complete(_reg_check):
        update["stage"] = "patient_agent"
        if _is_document:
            update["pending_action"] = "request_document"
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


def _extract_pending_appointment(text: str, state: dict) -> dict | None:
    """Parse the confirmation summary Eva sends and return structured appointment data.

    Summary format:
        Só confirmar antes de registrar: 😊
        📅 Terça-feira, dia 16/06, às 17:00
        👨‍⚕️ Dr. Júlio
        👤 Paciente: Paula Muniz
        📍 Modalidade: Presencial
    """
    import re as _re
    _CONFIRM_SUMMARY_MARKER = "Só confirmar antes de registrar"
    import re as _re_csa
    if not _re_csa.search(r'Só\s+(?:para\s+)?confirmar\s+antes\s+de\s+registrar', text):
        return None

    # Date + time
    date_match = _re.search(r'dia\s+(\d{1,2}/\d{2}).*?às\s+(\d{2}:\d{2})', text, _re.DOTALL)
    if not date_match:
        return None
    date_str = date_match.group(1)   # "16/06"
    time_str = date_match.group(2)   # "17:00"

    from datetime import date as _date
    from zoneinfo import ZoneInfo as _ZI
    _TZ_rec = _ZI("America/Recife")
    today = datetime.now(_TZ_rec).date()
    try:
        day, month = map(int, date_str.split('/'))
        year = today.year
        candidate = _date(year, month, day)
        if candidate < today:
            candidate = _date(year + 1, month, day)
    except (ValueError, OverflowError):
        return None

    slot_datetime = f"{candidate.isoformat()}T{time_str}:00"

    # Doctor
    if "Dr. Júlio" in text or "Dr. Julio" in text:
        doctor = "julio"
    elif "Dra. Bruna" in text:
        doctor = "bruna"
    else:
        doctor = state.get("preferred_doctor") or ""

    # Modality
    mod_match = _re.search(r'Modalidade[:\s]+(\w+)', text)
    modality = ""
    if mod_match:
        mod_raw = mod_match.group(1).lower()
        if mod_raw in ("presencial", "online"):
            modality = mod_raw

    # Duration
    p_age = state.get("patient_age") or 99
    is_returning = state.get("is_returning_patient")
    duration = 120 if (p_age < 18 and not is_returning and doctor == "julio") else 60

    return {
        "slot_datetime": slot_datetime,
        "slot_duration_minutes": duration,
        "modality": modality,
        "doctor": doctor,
    }


async def patient_agent_node(state: ConversationState, config: RunnableConfig) -> dict:
    """
    Single LLM call per turn. If the LLM returns tool calls, the graph routes
    to the ToolNode. When it returns plain text, it sends to WhatsApp and ends.
    """
    import logging as _log_pa
    _pa_logger = _log_pa.getLogger(__name__)

    # ── DB ↔ checkpoint sync for contact identity fields ─────────────────────
    # When the DB record is corrected after the conversation has started, the
    # checkpoint may still carry stale values (user_name == patient_name,
    # is_patient == True). Sync them here so Eva never addresses the wrong person.
    _sync_updates: dict = {}
    _user_db_id = state.get("user_db_id")

    # Fallback: if user_db_id is missing (e.g. checkpoint was reset), load from phone.
    # This ensures is_patient and user_name are always correct even after a state reset.
    if not _user_db_id and state.get("phone"):
        try:
            _fb_user = await get_user_by_phone(state["phone"])
            if _fb_user:
                _user_db_id = _fb_user["id"]
                _sync_updates["user_db_id"] = _user_db_id
                if not state.get("user_name") and _fb_user.get("name"):
                    _sync_updates["user_name"] = _fb_user["name"]
                if not state.get("patient_name") and _fb_user.get("patient_name"):
                    _sync_updates["patient_name"] = _fb_user["patient_name"]
                if state.get("is_patient") is None and _fb_user.get("is_patient") is not None:
                    _sync_updates["is_patient"] = _fb_user["is_patient"]
                if not state.get("preferred_doctor") and _fb_user.get("doctor_id"):
                    _sync_updates["preferred_doctor"] = DOCTOR_NAMES.get(_fb_user["doctor_id"])
                if not state.get("patient_email") and _fb_user.get("email"):
                    _sync_updates["patient_email"] = _fb_user["email"]
                if not state.get("is_returning_patient") and _fb_user.get("is_returning_patient") is not None:
                    _sync_updates["is_returning_patient"] = _fb_user["is_returning_patient"]
                _pa_logger.info("Fallback DB sync from phone for %s: %s", state["phone"], list(_sync_updates.keys()))
        except Exception:
            _pa_logger.exception("Fallback DB sync failed for %s", state.get("phone"))

    if _user_db_id:
        try:
            from app.database import get_supabase as _get_supabase_sync
            _db = await _get_supabase_sync()
            _db_user = await _db.from_("users").select("name,patient_name,is_patient").eq("id", _user_db_id).maybe_single().execute()
            if _db_user and _db_user.data:
                _db_name = _db_user.data.get("name")
                _db_is_patient = _db_user.data.get("is_patient")
                _db_patient_name = _db_user.data.get("patient_name")
                # Auto-correct is_patient if DB has True but names clearly differ
                if (
                    _db_is_patient is True
                    and _db_name and _db_patient_name
                    and _db_name.strip().lower() != _db_patient_name.strip().lower()
                ):
                    _db_is_patient = False
                    _pa_logger.warning(
                        "Auto-correcting is_patient=False for %s (name=%r != patient_name=%r)",
                        state["phone"], _db_name, _db_patient_name,
                    )
                    try:
                        await _db.from_("users").update({"is_patient": False}).eq("id", _user_db_id).execute()
                    except Exception:
                        _pa_logger.exception("Failed to auto-correct is_patient in DB for %s", state["phone"])
                if _db_name and _db_name != state.get("user_name"):
                    _sync_updates["user_name"] = _db_name
                if _db_is_patient is not None and _db_is_patient != state.get("is_patient"):
                    _sync_updates["is_patient"] = _db_is_patient
                if _db_patient_name and _db_patient_name != state.get("patient_name"):
                    _sync_updates["patient_name"] = _db_patient_name
                if _sync_updates:
                    _pa_logger.info("Syncing checkpoint with DB for %s: %s", state["phone"], _sync_updates)
        except Exception:
            _pa_logger.exception("Failed to sync checkpoint with DB for %s", state["phone"])

    # Apply sync updates to local state view so this turn uses the correct values
    if _sync_updates:
        state = {**state, **_sync_updates}

    # ── Programmatic confirmation bypass ─────────────────────────────────────
    # If Eva already sent the confirmation summary and the patient is now confirming,
    # call confirm_appointment DIRECTLY without going through the LLM.
    # This prevents: (a) LLM ignoring the guard and re-fetching slots,
    #                (b) double-booking when patient sends a non-standard affirmative
    #                    that the LLM interprets as confirmation without the guard.
    # Palavras-chave afirmativas para confirmar pending_appointment.
    # IMPORTANTE: a verificação usa word-boundary (re.search) para evitar falsos
    # positivos — ex: "ta" não deve bater em "quinta" ou "semana".
    _PENDING_AFFIRMATIVE = {
        "sim", "pode", "confirma", "confirmo", "ok", "isso", "pode ser",
        "tá", "ta", "tá bom", "ta bom", "perfeito", "ótimo", "otimo",
        "certo", "claro", "exato", "👍", "✅", "pode confirmar",
        "confirmar", "quero", "quero confirmar", "vai", "bora", "fechado",
    }
    # Palavras-chave negativas — se presentes, NÃO confirmar mesmo que haja
    # alguma palavra afirmativa na mesma mensagem.
    _PENDING_NEGATIVE = {
        "não", "nao", "cancela", "cancelar", "desculpe", "errado", "errei",
        "outro", "outra", "diferente", "mudei", "prefiro outro", "não quero",
    }
    _pending_appt = state.get("pending_appointment")
    if _pending_appt:
        import re as _re
        _last_msg = ""
        for _m in reversed(list(state["messages"])):
            if getattr(_m, "type", "") == "human":
                _last_msg = (getattr(_m, "content", "") or "").strip().lower()
                break

        def _word_match(keyword: str, text: str) -> bool:
            """True se keyword aparece como palavra inteira em text."""
            return bool(_re.search(r'(?<!\w)' + _re.escape(keyword) + r'(?!\w)', text))

        _has_negative = any(_word_match(w, _last_msg) for w in _PENDING_NEGATIVE)
        _has_affirmative = any(_word_match(w, _last_msg) for w in _PENDING_AFFIRMATIVE)

        if _last_msg and _has_affirmative and not _has_negative:
            _pa_logger.info("PENDING_APPT_CONFIRM slot=%s modality=%s", _pending_appt.get("slot_datetime"), _pending_appt.get("modality"))
            from app.graph.tools import confirm_appointment as _confirm_tool
            from app.whatsapp import send_text as _send_text
            from app.database import save_message as _save_msg
            try:
                _result = await _confirm_tool.coroutine(
                    slot_datetime=_pending_appt["slot_datetime"],
                    slot_duration_minutes=_pending_appt["slot_duration_minutes"],
                    modality=_pending_appt.get("modality", ""),
                    session_note="",
                    force_encaixe=False,
                    patient_name_override="",
                    state=state,
                    config=config,
                )
            except Exception:
                _pa_logger.exception("PENDING_APPT_CONFIRM failed")
                _result = "Erro ao confirmar o agendamento. Tente novamente."

            # Convert semantic codes to patient-friendly messages
            _contact_name = (state.get("user_name") or "").split()[0] or "você"
            # confirm_appointment success codes may be prefixed with the internal-instruction tag.
            # Strip it before matching, otherwise a successful booking is misread as an error.
            _INT_PREFIX = "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
            _result_body = _result[len(_INT_PREFIX):] if _result.startswith(_INT_PREFIX) else _result
            if _result_body.startswith("AGENDAMENTO_OK\n") or _result_body.startswith("AGENDAMENTO_TAXA_DISPENSADA\n") or _result_body.startswith("AGENDAMENTO_CORTESIA\n"):
                # Extract doctor/date/time line (2nd line)
                _lines = _result_body.splitlines()
                _appt_line = _lines[1] if len(_lines) > 1 else ""
                if _result_body.startswith("AGENDAMENTO_CORTESIA\n"):
                    _patient_msg = (
                        f"Perfeito, {_contact_name}! 😊 Consulta confirmada:\n{_appt_line}\n\n"
                        f"Como combinado, a taxa de reserva está isenta. Até lá!"
                    )
                elif _result_body.startswith("AGENDAMENTO_TAXA_DISPENSADA\n"):
                    _patient_msg = (
                        f"Perfeito, {_contact_name}! 😊 Consulta confirmada:\n{_appt_line}\n\n"
                        f"A taxa de reserva foi dispensada. Até lá!"
                    )
                else:
                    _PIX_KEY = "42006848000178"
                    _patient_msg = (
                        f"Consulta registrada! ✅\n{_appt_line}\n\n"
                        f"Para garantir a vaga, é necessário o pagamento da taxa de reserva de R$ 100,00 em até 2 horas.\n"
                        f"💳 PIX: {_PIX_KEY}\n\n"
                        f"Esse valor será abatido do total da consulta. Em caso de cancelamento com menos de 24h de antecedência ou ausência sem justificativa, a taxa não é devolvida."
                    )
            elif _result.startswith("[INSTRUÇÃO INTERNA"):
                # Before flagging an error, check if patient already has this appointment
                # scheduled (bypass fired twice — e.g. patient said "Ok" after PIX message).
                _already_booked = False
                try:
                    from app.database import get_supabase as _get_sb
                    _sb = await _get_sb()
                    from app.database import get_users_by_phone as _get_users
                    _users = await _get_users(state["phone"])
                    _uids = [u["id"] for u in _users]
                    if _uids:
                        _slot_dt_check = _pending_appt.get("slot_datetime", "")
                        _dup_check = await _sb.from_("appointments").select("appointment_id, start_time").in_("patient_id", _uids).eq("status", "scheduled").execute()
                        from datetime import datetime as _dtt
                        from zoneinfo import ZoneInfo as _ZI
                        _slot_parsed = _dtt.fromisoformat(_slot_dt_check).astimezone(_ZI("America/Recife")) if _slot_dt_check else None
                        for _row in (_dup_check.data or []):
                            _row_dt = _dtt.fromisoformat(_row["start_time"]).astimezone(_ZI("America/Recife"))
                            if _slot_parsed and abs((_row_dt - _slot_parsed).total_seconds()) < 60:
                                _already_booked = True
                                break
                except Exception:
                    pass
                if _already_booked:
                    _doctor_lbl2 = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(_pending_appt.get("doctor", ""), "médico(a)")
                    _patient_msg = f"Sua consulta já está confirmada com {_doctor_lbl2}! 😊 Qualquer dúvida, estou à disposição."
                else:
                    _patient_msg = f"Ops, {_contact_name}! Tive um problema ao confirmar o agendamento. Nossa equipe já foi notificada. 😊"
            else:
                _patient_msg = _result

            await _send_text(state["phone"], _patient_msg)
            await _save_msg(state["phone"], "assistant", _patient_msg)
            from langchain_core.messages import AIMessage as _AI, ToolMessage as _TM
            import uuid as _uuid_pa
            _tc_id_pa = str(_uuid_pa.uuid4())
            _ai_with_tool = _AI(content="", tool_calls=[{"name": "confirm_appointment", "args": {}, "id": _tc_id_pa, "type": "tool_use"}])
            _tool_msg = _TM(content=_result, tool_call_id=_tc_id_pa)
            return {"pending_appointment": None, "messages": [_ai_with_tool, _tool_msg, _AI(content=_patient_msg)], "silent_mode": False, **_sync_updates}

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

    # Inject contact-vs-patient rule when the WhatsApp contact is NOT the patient.
    # Without this, the LLM defaults to using patient_name (prominent in the prompt header)
    # to address the contact, even when they are different people.
    if state.get("is_patient") is False and state.get("user_name"):
        contact_first = contact_name.split()[0]
        system_prompt += (
            f"\n\nIMPORTANTE — CONTATO ≠ PACIENTE: Quem está no WhatsApp é *{contact_name}* "
            f"(o contato/responsável), NÃO o paciente *{first_name}*. "
            f"Em TODAS as mensagens, dirija-se a {contact_first} pelo nome. "
            f"Use o nome {first_name} apenas quando falar SOBRE o paciente na terceira pessoa "
            f"(ex: 'a consulta do {first_name}', 'o horário do {first_name}')."
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
        _pending = state.get("pending_appointment")
        if _pending:
            import json as _json
            system_prompt += (
                f"\n\nLEMBRETE (modo atendente): Há um agendamento pendente aguardando confirmação: "
                f"{_json.dumps(_pending, ensure_ascii=False)}. "
                "Se a instrução da atendente for confirmar o agendamento (ex: 'confirme', 'a paciente confirmou', "
                "'seguir fluxo'), você DEVE chamar confirm_appointment IMEDIATAMENTE com os dados acima — "
                "não chame get_available_slots nem peça mais informações."
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

    # Inject upcoming/recent appointments so the LLM knows what already exists
    upcoming = await get_upcoming_appointments(state["phone"])
    if upcoming:
        from zoneinfo import ZoneInfo as _ZI
        _TZ = _ZI("America/Recife")
        future_lines = []
        recent_lines = []
        for apt in upcoming:
            dt = datetime.fromisoformat(apt["start_time"]).astimezone(_TZ)
            fee_ok = apt.get("booking_fee_paid_at") or apt.get("booking_fee_waived")
            fee_tag = "" if fee_ok else " ⚠️ TAXA DE RESERVA PENDENTE"
            label = f"- {dt.strftime('%d/%m/%Y às %H:%M')} (ID: {apt['appointment_id']}){fee_tag}"
            if apt.get("recently_ended"):
                recent_lines.append(label)
            else:
                future_lines.append(label)
        if future_lines:
            system_prompt += "\n\nConsultas agendadas para este paciente:\n" + "\n".join(future_lines)
        if recent_lines:
            system_prompt += "\n\nConsulta(s) recém-realizada(s) (nas últimas 48h):\n" + "\n".join(recent_lines)

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

    # Detect when the last AI message was the appointment confirmation summary
    # ("Só confirmar antes de registrar") and the patient just replied affirmatively.
    # In this situation the ONLY correct next action is confirm_appointment.
    # Without this guard, the NEW_PATIENT_SYSTEM step-1 instruction ("if the user
    # mentioned a day, call get_available_slots immediately") fires on the human's
    # confirmation reply and Eva re-queries slots instead of confirming.
    import re as _re_marker
    _CONFIRM_SUMMARY_RE = _re_marker.compile(r'Só\s+(?:para\s+)?confirmar\s+antes\s+de\s+registrar')
    # 🙏 is a thank-you gesture, NOT a booking confirmation — keep it out of this set
    _AFFIRMATIVE = {"sim", "pode", "confirma", "confirmo", "ok", "isso", "pode ser",
                    "tá", "ta", "tá bom", "ta bom", "perfeito", "ótimo", "otimo",
                    "certo", "claro", "exato", "👍", "✅", "pode confirmar",
                    "confirmar", "quero", "quero confirmar", "vai", "bora", "fechado"}
    _last_human_content = ""
    for _m in reversed(clean_messages):
        if not _last_human_content and getattr(_m, "type", "") == "human":
            _last_human_content = (getattr(_m, "content", "") or "").strip().lower()
            break
    # Search only the last 6 messages for the confirmation summary marker.
    # Searching all history causes false positives: an old "Só confirmar antes de registrar"
    # from a previous booking would trigger the guard on an unrelated "Pode confirmar"
    # reply to a reminder message.
    _summary_in_recent_ai = any(
        bool(_CONFIRM_SUMMARY_RE.search(getattr(_m, "content", "") or ""))
        for _m in clean_messages[-6:]
        if getattr(_m, "type", "") == "ai"
    )
    # Do NOT re-fire the guard if confirm_appointment was already called successfully
    # in this conversation (prevents double-booking when patient sends a non-affirmative
    # gesture like 🙏 that the LLM interprets as confirmation and books the slot).
    _already_confirmed = any(
        getattr(_m, "type", "") == "tool"
        and "registrada" in (getattr(_m, "content", "") or "").lower()
        and any(
            tc.get("name") == "confirm_appointment"
            for ai_m in clean_messages
            if getattr(ai_m, "type", "") == "ai"
            for tc in (getattr(ai_m, "tool_calls", None) or [])
        )
        for _m in clean_messages
    )
    _awaiting_confirm = (
        _summary_in_recent_ai
        and any(w in _last_human_content for w in _AFFIRMATIVE)
        and not _already_confirmed
    )
    if _awaiting_confirm:
        _confirm_instruction = (
            "⚠️ ATENÇÃO — AÇÃO ÚNICA OBRIGATÓRIA AGORA: "
            "O paciente acabou de confirmar o agendamento que você apresentou na mensagem anterior. "
            "Você JÁ tem todos os dados necessários no histórico da conversa (data, hora, médico, paciente, modalidade). "
            "Chame confirm_appointment IMEDIATAMENTE com esses dados. "
            "NÃO chame get_available_slots. NÃO faça perguntas. NÃO busque novos horários. "
            "Apenas chame confirm_appointment agora.\n\n"
        )
        system_prompt = _confirm_instruction + system_prompt

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

    # ── Guard: block premature confirm_appointment ────────────────────────────
    # If the LLM is trying to call confirm_appointment but the patient has NOT
    # yet confirmed (pending_appointment is not set), intercept the call:
    # extract the slot data from the tool args, send the confirmation summary
    # to the patient, and store pending_appointment — bypassing the tool call.
    # Exception: attendant-mode (silent_mode) skips this guard.
    if (
        response.tool_calls
        and not state.get("silent_mode")
        and not state.get("pending_appointment")
        and any(tc.get("name") == "confirm_appointment" for tc in response.tool_calls)
    ):
        _confirm_tc = next(tc for tc in response.tool_calls if tc.get("name") == "confirm_appointment")
        _args = _confirm_tc.get("args") or {}
        _slot_dt = _args.get("slot_datetime", "")
        _duration = _args.get("slot_duration_minutes", 60)
        _modality = _args.get("modality", "")

        # Build the human-readable summary from the slot datetime
        _doctor_key = state.get("preferred_doctor") or ""
        _doctor_lbl = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(_doctor_key, "médico(a)")
        _patient_nm = state.get("patient_name") or state.get("user_name") or "Paciente"
        _mod_lbl = {"presencial": "Presencial", "online": "Online"}.get(_modality, _modality.capitalize() if _modality else "—")

        _summary = ""
        try:
            from datetime import datetime as _dt2
            from zoneinfo import ZoneInfo as _ZI2
            _slot = _dt2.fromisoformat(_slot_dt).astimezone(_ZI2("America/Recife"))
            _weekdays_pt = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
            _weekday_name = _weekdays_pt[_slot.weekday()]
            _summary = (
                f"Só confirmar antes de registrar: 😊\n"
                f"📅 {_weekday_name}, dia {_slot.strftime('%d/%m')}, às {_slot.strftime('%H:%M')}\n"
                f"👨‍⚕️ {_doctor_lbl}\n"
                f"👤 Paciente: {_patient_nm}\n"
                f"📍 Modalidade: {_mod_lbl}\n"
                f"Posso confirmar o agendamento?"
            )
        except Exception:
            _logger.exception("GUARD_PREMATURE_CONFIRM: failed to build summary for slot=%s", _slot_dt)

        if _summary:
            _logger.info("GUARD_PREMATURE_CONFIRM: intercepted confirm_appointment, sending summary instead. slot=%s", _slot_dt)
            await send_text(state["phone"], _summary)
            await save_message(state["phone"], "assistant", _summary)
            from langchain_core.messages import AIMessage as _AIMsg2
            _pending_from_args = {
                "slot_datetime": _slot_dt,
                "slot_duration_minutes": _duration,
                "modality": _modality,
                "doctor": _doctor_key,
            }
            return {
                "messages": [_AIMsg2(content=_summary)],
                "pending_appointment": _pending_from_args,
            }

    # ── Guard: catch promised-but-not-called transfer_to_human ──────────────
    # If the LLM said "vou transferir" in its text but produced no tool calls,
    # force-call transfer_to_human so the handoff actually happens.
    # Skip in silent_mode (attendant instructions) — transfer is blocked there.
    if (
        not response.tool_calls
        and not state.get("silent_mode")
        and response.content
    ):
        _transfer_phrases = (
            "vou transferir",
            "vou te transferir",
            "transferindo para",
            "vou encaminhar",
        )
        _resp_lower = response.content.lower()
        _promised_transfer = any(p in _resp_lower for p in _transfer_phrases)
        if _promised_transfer:
            _logger.info("GUARD_TRANSFER: LLM promised transfer but called no tool — forcing transfer_to_human")
            # Send Eva's message to the patient first, then execute the transfer tool
            await send_text(state["phone"], response.content)
            await save_message(state["phone"], "assistant", response.content)
            from langchain_core.messages import AIMessage as _AIMsg3, ToolMessage as _TMsg3
            import uuid as _uuid3
            _tc_id = str(_uuid3.uuid4())
            _forced_tc = {"name": "transfer_to_human", "args": {"reason": "Eva prometeu transferir mas não chamou a tool — transferência forçada pelo guard."}, "id": _tc_id, "type": "tool_use"}
            _forced_ai = _AIMsg3(content=response.content, tool_calls=[_forced_tc])
            from app.graph.tools import transfer_to_human as _transfer_fn
            _transfer_result = await _transfer_fn(
                reason="Paciente aguarda atendimento. Eva prometeu transferir.",
                state=state,
                config=config,
            )
            _tool_msg = _TMsg3(content=str(_transfer_result), tool_call_id=_tc_id)
            return {"messages": [_forced_ai, _tool_msg]}

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

    update: dict = {"messages": [response], "silent_mode": False, **_sync_updates}
    if pending_action:
        update["pending_action"] = None

    # If the LLM just sent the confirmation summary, extract and store the slot data
    # so the next turn can confirm directly without going through the LLM again.
    if not response.tool_calls and response.content:
        _parsed = _extract_pending_appointment(response.content, state)
        if _parsed:
            update["pending_appointment"] = _parsed
            _logger.info("PENDING_APPT_STORED slot=%s", _parsed.get("slot_datetime"))
        elif state.get("pending_appointment"):
            # LLM replied with something other than a new summary — clear stale pending
            update["pending_appointment"] = None

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
