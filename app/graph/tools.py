import asyncio
import os
from datetime import datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState

import logging

from app.whatsapp import send_text
from app.database import get_supabase, log_event, upsert_user, get_user_by_phone, get_users_by_phone, DOCTOR_IDS, DOCTOR_NAMES
from app.chatwoot import get_conversation_id, unassign_agent_bot, add_label

logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/Recife")

APPOINTMENT_NOTIFY_PHONE = os.getenv("APPOINTMENT_NOTIFY_PHONE", "")


async def _notify_clinic(message: str, phone: str = "", subject: str = "Notificação Eva") -> None:
    """Envia notificação para a clínica via WhatsApp, nota privada no Chatwoot e e-mail."""
    import asyncio as _asyncio
    from app.email_sender import send_clinic_notification_email

    tasks = []

    # WhatsApp para número externo (opcional)
    if APPOINTMENT_NOTIFY_PHONE:
        async def _wa():
            try:
                await send_text(APPOINTMENT_NOTIFY_PHONE, message)
            except Exception:
                pass
        tasks.append(_wa())

    # Nota privada no Chatwoot (na conversa do paciente)
    if phone:
        from app.chatwoot import get_conversation_id, add_private_note
        conv_id = get_conversation_id(phone)
        if conv_id is not None:
            async def _note():
                try:
                    await add_private_note(conv_id, message)
                except Exception:
                    pass
            tasks.append(_note())

    # E-mail para a clínica
    async def _email():
        try:
            await send_clinic_notification_email(subject, message)
        except Exception:
            pass
    tasks.append(_email())

    if tasks:
        await _asyncio.gather(*tasks)


def _build_registration_block(state: dict) -> str:
    """Return a formatted registration summary for new patients, or empty string."""
    if state.get("is_patient"):
        return ""

    phone_raw = ""  # phone is in config, not state — caller adds it if needed
    lines = ["\n\n📋 CADASTRO DO PACIENTE:"]

    contact = state.get("user_name") or ""
    patient = state.get("patient_name") or ""
    is_for_self = state.get("is_for_self")

    if is_for_self is False and contact and contact != patient:
        lines.append(f"  Responsável: {contact}")

    lines.append(f"  Nome: {patient or '—'}")
    lines.append(f"  Idade: {state.get('patient_age') or '—'}")
    lines.append(f"  Data de nascimento: {state.get('birth_date') or '—'}")
    lines.append(f"  CPF paciente: {state.get('patient_cpf') or '—'}")
    lines.append(f"  E-mail: {state.get('patient_email') or '—'}")

    guardian_name = state.get("guardian_name")
    guardian_cpf = state.get("guardian_cpf")
    if guardian_name:
        lines.append(f"  Responsável legal: {guardian_name}")
    if guardian_cpf:
        lines.append(f"  CPF responsável: {guardian_cpf}")

    reason = state.get("consultation_reason")
    referral = state.get("referral_professional")
    if reason:
        lines.append(f"  Motivo da consulta: {reason}")
    if referral:
        lines.append(f"  Encaminhado por: {referral}")

    return "\n".join(lines)


async def _resolve_doctor(state: dict, config: RunnableConfig) -> str:
    """Return preferred_doctor key, falling back to DB if not in state."""
    doctor = state.get("preferred_doctor") or ""
    if not doctor:
        user = await get_user_by_phone(config["configurable"]["phone"])
        if user and user.get("doctor_id"):
            doctor = DOCTOR_NAMES.get(user["doctor_id"], "")
    return doctor


async def _get_doctor_calendar_id(preferred_doctor: str) -> str | None:
    """Fetch agenda_id (Google Calendar ID) for a doctor from Supabase."""
    doctor_id = DOCTOR_IDS.get(preferred_doctor)
    if not doctor_id:
        return None
    client = await get_supabase()
    result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
    return result.data.get("agenda_id") if result.data else None


_WEEKDAY_LABELS_PT = {
    0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
    3: "quinta-feira",  4: "sexta-feira",  5: "sábado", 6: "domingo",
}

_MOD_LABELS = {
    "online": "apenas online",
    "escolha": "online ou presencial — paciente escolhe livremente",
    "presencial_sob_consulta": "REQUER CONFIRMAÇÃO — online ou presencial sob consulta da atendente",
}


@tool
async def get_available_slots(
    preferred_day: str,
    preferred_shift: Literal["manha", "tarde", "noite", "qualquer"],
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """
    Busca horários disponíveis no Google Calendar para o médico do paciente.
    Quando preferred_day for um dia da semana (ex: "quarta"), a ferramenta busca
    automaticamente nas próximas semanas até encontrar um horário disponível (máx. 4 semanas).
    Use slot_duration_minutes=120 para primeira consulta de paciente menor de 18 anos,
    60 para todos os outros casos.
    Use preferred_shift="qualquer" quando o paciente informar um dia mas ainda não tiver
    dito preferência de turno — isso verifica todos os turnos e retorna os disponíveis
    para que você possa apresentar opções reais ao paciente antes de perguntar o turno.
    """
    from app.google_calendar import get_available_slots as _get_slots, _parse_day, DOCTOR_SCHEDULES, SHIFT_HOURS, _WEEKDAYS_PT

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    # Dra. Bruna only attends patients aged 12 or older
    if doctor == "bruna" and (state.get("patient_age") or 99) < 12:
        return (
            "Dra. Bruna atende apenas pacientes a partir de 12 anos. "
            "Este paciente tem menos de 12 anos e precisa ser atendido pelo Dr. Júlio. "
            "Por favor, informe o paciente e pergunte se deseja agendar com o Dr. Júlio."
        )

    # Dr. Júlio only attends patients up to 65 years old
    if doctor == "julio" and (state.get("patient_age") or 0) > 65:
        return (
            "Dr. Júlio atende pacientes até 65 anos. "
            "Este paciente tem mais de 65 anos e precisa ser atendido pela Dra. Bruna. "
            "Por favor, informe o paciente e pergunte se deseja agendar com a Dra. Bruna."
        )

    # Dra. Bruna always uses 1h slots regardless of patient age
    if doctor == "bruna":
        slot_duration_minutes = 60

    # ── "qualquer" shift: check all shifts and return summary ─────────────────
    if preferred_shift == "qualquer":
        target_date = _parse_day(preferred_day)
        if target_date is None:
            return "Não entendi a data. Por favor informe um dia específico (ex: segunda, 19/05, amanhã)."
        day_of_week = _WEEKDAY_LABELS_PT.get(target_date.weekday(), "")
        date_label = target_date.strftime("%d/%m")
        header = f"{day_of_week}, dia {date_label}" if day_of_week else date_label
        sections = []
        for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
            slots = await _get_slots(
                calendar_id=calendar_id,
                preferred_day=preferred_day,
                preferred_shift=shift_key,
                slot_minutes=slot_duration_minutes,
                doctor_key=doctor,
            )
            if slots:
                times = ", ".join(s[0].strftime("%H:%M") for s in slots)
                sections.append(f"- {shift_label.capitalize()}: {times}")
        if not sections:
            return f"Não há horários disponíveis para {header}. Deseja tentar outro dia?"
        return f"Horários disponíveis para {header}:\n" + "\n".join(sections)

    now = datetime.now(TZ)
    shift_norm = preferred_shift.lower().replace("ã", "a").replace("manhã", "manha").strip()
    shift_start_h, shift_end_h = SHIFT_HOURS.get(shift_norm, (8, 18))

    # ── Detect weekday name → multi-week search ───────────────────────────────
    preferred_day_norm = preferred_day.lower().strip()
    weekday_key = next(
        (wd for name, wd in _WEEKDAYS_PT.items() if name in preferred_day_norm),
        None,
    )

    # ── Vague expressions (e.g. "próxima semana") → ask for specific day ─────
    _vague_patterns = ("semana", "mês", "mes", "em breve", "qualquer", "tanto faz")
    if weekday_key is None and any(p in preferred_day_norm for p in _vague_patterns):
        return (
            "CLARIFICAÇÃO NECESSÁRIA: O paciente disse uma expressão vaga (ex: 'próxima semana'). "
            "Pergunte qual dia da semana prefere (segunda a sexta) antes de chamar get_available_slots novamente."
        )

    if weekday_key is not None:
        # Verify doctor works this weekday/shift at all before iterating
        day_windows = DOCTOR_SCHEDULES.get(doctor, {}).get(weekday_key, [])
        if not any(entry[0] < shift_end_h and entry[2] > shift_start_h for entry in day_windows):
            day_label = _WEEKDAY_LABELS_PT.get(weekday_key, preferred_day)
            return (
                f"O médico não atende no turno da {preferred_shift} na {day_label}. "
                "Deseja tentar outro turno ou outro dia?"
            )

        base_date = _parse_day(preferred_day)  # nearest future occurrence
        day_label = _WEEKDAY_LABELS_PT.get(weekday_key, preferred_day)

        for week_offset in range(4):
            try_date = base_date + timedelta(weeks=week_offset)
            slots = await _get_slots(
                calendar_id=calendar_id,
                preferred_day=try_date.isoformat(),
                preferred_shift=preferred_shift,
                slot_minutes=slot_duration_minutes,
                doctor_key=doctor,
            )
            if slots:
                date_str = try_date.strftime("%d/%m")
                lines = [f"Horários disponíveis para {day_label}, dia {date_str} ({preferred_shift}):"]
                for i, (slot, modality) in enumerate(slots, 1):
                    lines.append(f"{i}. {slot.strftime('%H:%M')} [{_MOD_LABELS.get(modality, modality)}]")
                return "\n".join(lines)
            # No slots this week — silently try the next one

        return (
            f"Não encontrei horários disponíveis para {day_label} no turno da {preferred_shift} "
            "nas próximas 4 semanas. Deseja tentar outro turno ou outro dia?"
        )

    # ── Specific day (hoje, amanhã, ISO date): single attempt ─────────────────
    target_date = _parse_day(preferred_day)
    min_advance = now + timedelta(hours=4)

    slots = await _get_slots(
        calendar_id=calendar_id,
        preferred_day=preferred_day,
        preferred_shift=preferred_shift,
        slot_minutes=slot_duration_minutes,
        doctor_key=doctor,
    )

    if not slots:
        if target_date is not None and target_date == now.date():
            doctor_windows = DOCTOR_SCHEDULES.get(doctor, {}).get(target_date.weekday(), [])
            shift_has_windows = any(
                entry[0] < shift_end_h and entry[2] > shift_start_h
                for entry in doctor_windows
            )
            if shift_has_windows and min_advance.hour < shift_end_h:
                return (
                    "AGENDAMENTO_URGENTE: O paciente quer um horário hoje dentro das próximas 4 horas. "
                    "Não tenho permissão para agendar com tão pouca antecedência — apenas a atendente "
                    "pode verificar disponibilidade para encaixes urgentes. "
                    "Use transfer_to_human para encaminhar ao atendente humano."
                )
        return f"Não há horários disponíveis para {preferred_day} no turno da {preferred_shift}. Deseja tentar outro dia ou turno?"

    day_of_week = _WEEKDAY_LABELS_PT.get(target_date.weekday(), "") if target_date else ""
    date_label = target_date.strftime("%d/%m") if target_date else preferred_day
    header = f"{day_of_week}, dia {date_label}" if day_of_week else date_label
    lines = [f"Horários disponíveis para {header} ({preferred_shift}):"]
    for i, (slot, modality) in enumerate(slots, 1):
        lines.append(f"{i}. {slot.strftime('%H:%M')} [{_MOD_LABELS.get(modality, modality)}]")

    return "\n".join(lines)


@tool
async def confirm_appointment(
    slot_datetime: str,
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    session_note: str = "",
    modality: str = "",
) -> str:
    """
    Confirma e cria o agendamento no Google Calendar.
    slot_datetime deve estar no formato ISO 8601, ex: '2026-03-19T09:00:00'.
    session_note: use para identificar sessões separadas de menor de idade,
      ex: '1ª hora — responsáveis' ou '2ª hora — paciente'.
      Deixe vazio para consultas normais ou consultas de 2h em bloco único.
    modality: modalidade de atendimento — "online" ou "presencial".
      Para slots marcados como "apenas online" na listagem, passe "online".
      Para slots com escolha livre, passe o que o paciente escolheu.
      Para slots "presencial requer confirmação": se o paciente escolheu presencial,
      use transfer_to_human antes de chamar confirm_appointment.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    from app.google_calendar import create_event

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    _logger.info("CONFIRM_DEBUG calendar_id=%s doctor=%s slot=%s duration=%s",
                 calendar_id, doctor, slot_datetime, slot_duration_minutes)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    try:
        start = datetime.fromisoformat(slot_datetime).replace(tzinfo=TZ)
    except ValueError:
        return f"Formato de data inválido: {slot_datetime}. Use ISO 8601 (ex: 2026-03-19T09:00:00)."

    # Double-check slot is still free before booking
    from app.google_calendar import _get_busy, _credentials
    from googleapiclient.discovery import build as _build
    slot_end_check = start + timedelta(minutes=slot_duration_minutes)
    try:
        _creds = _credentials()
        _service = _build("calendar", "v3", credentials=_creds)
        loop = asyncio.get_event_loop()
        busy = await loop.run_in_executor(None, _get_busy, _service, calendar_id, start, slot_end_check)
        if busy:
            return (
                f"Este horário ({start.strftime('%d/%m/%Y às %H:%M')}) acabou de ser ocupado. "
                "Chame get_available_slots novamente para buscar outro horário disponível."
            )
    except Exception:
        pass  # If check fails, proceed anyway — better to double-book than block

    # Enforce modality constraints from schedule
    from app.google_calendar import get_modality_for_slot
    slot_constraint = get_modality_for_slot(doctor, start)
    if slot_constraint == "online":
        effective_modality = "online"
    elif slot_constraint == "presencial_sob_consulta" and modality == "presencial":
        if state.get("silent_mode"):
            # Running under an attendant instruction — attendant has already confirmed availability
            effective_modality = "presencial"
        else:
            return (
                "AÇÃO NECESSÁRIA: Este horário (quinta à tarde com o Dr. Júlio) pode ser presencial, "
                "mas a disponibilidade precisa ser confirmada pela atendente. "
                "Use transfer_to_human para que ela confirme antes de prosseguir."
            )
    else:
        effective_modality = modality if modality in ("online", "presencial") else ""

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        state.get("preferred_doctor", ""), "médico(a)"
    )
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    patient_age = state.get("patient_age") or 99
    # is_minor_first only applies to a single 2h block (no session_note)
    is_minor_first = (
        patient_age < 18
        and not state.get("is_patient", False)
        and state.get("preferred_doctor") == "julio"
        and not session_note
        and slot_duration_minutes == 120
    )

    _logger.info("CONFIRM_DEBUG2 patient=%s calendar=%s start=%s modality=%s", patient_name, calendar_id, start, effective_modality)

    try:
        event_id = await create_event(
            calendar_id=calendar_id,
            start=start,
            slot_minutes=slot_duration_minutes,
            patient_name=patient_name,
            doctor_name=doctor_label,
            is_minor_first=is_minor_first,
            session_note=session_note,
            modality=effective_modality,
        )
    except Exception as e:
        _logger.error("CONFIRM_DEBUG create_event FAILED: %s", e, exc_info=True)
        return f"Erro ao criar evento no Google Calendar: {e}"

    formatted = start.strftime("%d/%m/%Y às %H:%M")
    phone = config["configurable"]["phone"]

    # Persist to appointments table; roll back calendar event on failure
    end = start + timedelta(minutes=slot_duration_minutes)
    user = await get_user_by_phone(phone)
    client = await get_supabase()
    try:
        await client.from_("appointments").insert({
            "user_id": user["id"] if user else None,
            "doctor_id": DOCTOR_IDS.get(state.get("preferred_doctor", "")),
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
        }).execute()
    except Exception:
        from app.google_calendar import cancel_event
        try:
            await cancel_event(calendar_id, event_id)
        except Exception:
            pass
        return "Houve um erro ao salvar o agendamento. Por favor, tente novamente."

    await log_event("appointment_booked", phone, {
        "doctor": state.get("preferred_doctor"),
        "datetime": slot_datetime,
        "duration_minutes": slot_duration_minutes,
        "patient_name": patient_name,
        "session_note": session_note,
    })

    session_label = f" ({session_note})" if session_note else ""
    modality_line = f"\nModalidade: {'Online' if effective_modality == 'online' else 'Presencial'}" if effective_modality else ""
    patient_email = state.get("patient_email") or "não informado"
    registration_block = _build_registration_block(state)
    asyncio.create_task(_notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {patient_name}{session_label}\n"
        f"Data e horário: {formatted}\n"
        f"Médico(a): {doctor_label}"
        f"{modality_line}\n\n"
        f"📋 LEMBRETE: enviar o Termo de Compromisso para o e-mail do paciente ({patient_email})."
        f"{registration_block}",
        phone=phone,
        subject=f"Agendamento realizado — {patient_name}",
    ))

    return f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}{session_label}\nID: {event_id}"


@tool
async def cancel_appointment(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Cancela uma consulta agendada. appointment_id é o Google Calendar event ID."""
    from app.google_calendar import cancel_event

    calendar_id = await _get_doctor_calendar_id(state.get("preferred_doctor", ""))
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    # Fetch appointment data before canceling for the notification
    client = await get_supabase()
    appt_result = await client.from_("appointments").select("start_time").eq("appointment_id", appointment_id).maybe_single().execute()
    old_start_time = appt_result.data.get("start_time") if appt_result.data else None

    # Cancel in Google Calendar
    await cancel_event(calendar_id, appointment_id)

    # Update status in DB
    await client.from_("appointments").update({
        "status": "canceled",
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    phone = config["configurable"]["phone"]
    await log_event("appointment_canceled", phone, {"appointment_id": appointment_id})

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        state.get("preferred_doctor", ""), "médico(a)"
    )
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    if old_start_time:
        old_dt = datetime.fromisoformat(old_start_time).astimezone(TZ)
        formatted_old = old_dt.strftime("%d/%m/%Y às %H:%M")
    else:
        formatted_old = "horário não disponível"

    asyncio.create_task(_notify_clinic(
        f"Agendamento cancelado! ❌\n"
        f"Paciente: {patient_name}\n"
        f"Data e horário: {formatted_old}\n"
        f"Médico(a): {doctor_label}",
        phone=phone,
        subject=f"Agendamento cancelado — {patient_name}",
    ))

    return "Consulta cancelada com sucesso. ✅"


@tool
async def reschedule_appointment(
    appointment_id: str,
    new_slot_datetime: str,
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    modality: str = "",
) -> str:
    """
    Remarca uma consulta existente para um novo horário.
    appointment_id é o Google Calendar event ID.
    new_slot_datetime deve estar no formato ISO 8601, ex: '2026-03-19T09:00:00'.
    modality: modalidade do novo horário — "online" ou "presencial" (se aplicável).
    """
    from app.google_calendar import update_event

    calendar_id = await _get_doctor_calendar_id(state.get("preferred_doctor", ""))
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    try:
        new_start = datetime.fromisoformat(new_slot_datetime).replace(tzinfo=TZ)
    except ValueError:
        return f"Formato de data inválido: {new_slot_datetime}. Use ISO 8601 (ex: 2026-03-19T09:00:00)."

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        state.get("preferred_doctor", ""), "médico(a)"
    )
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    patient_age = state.get("patient_age") or 99
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)

    # Fetch old start_time before updating
    client = await get_supabase()
    appt_result = await client.from_("appointments").select("start_time").eq("appointment_id", appointment_id).maybe_single().execute()
    old_start_time = appt_result.data.get("start_time") if appt_result.data else None

    # Enforce modality constraints
    from app.google_calendar import get_modality_for_slot
    slot_constraint = get_modality_for_slot(state.get("preferred_doctor", ""), new_start)
    effective_modality = "online" if slot_constraint == "online" else (modality if modality in ("online", "presencial") else "")

    # Update Google Calendar event (same event_id, new time)
    await update_event(
        calendar_id=calendar_id,
        event_id=appointment_id,
        new_start=new_start,
        slot_minutes=slot_duration_minutes,
        patient_name=patient_name,
        doctor_name=doctor_label,
        is_minor_first=is_minor_first,
        modality=effective_modality,
    )

    # Update DB record
    new_end = new_start + timedelta(minutes=slot_duration_minutes)
    await client.from_("appointments").update({
        "start_time": new_start.isoformat(),
        "end_time": new_end.isoformat(),
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    phone = config["configurable"]["phone"]
    formatted_new = new_start.strftime("%d/%m/%Y às %H:%M")
    await log_event("appointment_rescheduled", phone, {
        "appointment_id": appointment_id,
        "new_datetime": new_slot_datetime,
    })

    if old_start_time:
        old_dt = datetime.fromisoformat(old_start_time).astimezone(TZ)
        formatted_old = old_dt.strftime("%d/%m/%Y às %H:%M")
    else:
        formatted_old = "horário não disponível"

    asyncio.create_task(_notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {patient_name}\n"
        f"Horário anterior: {formatted_old}\n"
        f"Novo horário: {formatted_new}\n"
        f"Médico(a): {doctor_label}",
        phone=phone,
        subject=f"Agendamento alterado — {patient_name}",
    ))

    return f"Consulta remarcada com sucesso! ✅\n{doctor_label} — {formatted_new}"


@tool
async def request_document(
    document_type: Literal["nota_fiscal", "laudo", "exame", "relatorio", "receita", "declaracao"],
    patient_email: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    medication_note: str = "",
) -> str:
    """Registra uma solicitação de documento médico para o paciente.
    patient_email: e-mail informado pelo paciente para recebimento do documento.
    medication_note: obrigatório quando document_type='receita' — medicação(ões) solicitada(s).
    """
    import logging as _log
    _log.getLogger(__name__).warning("REQUEST_DOC_CALLED type=%s email=%s", document_type, patient_email)

    # Fall back to state if LLM didn't pass medication_note explicitly
    if not medication_note.strip():
        medication_note = state.get("medication_note") or ""

    if document_type == "receita" and not medication_note.strip():
        return "Qual medicação você precisa na receita?"

    from app.google_sheets import append_document_request, get_controlled_medications
    from app.email_sender import send_document_request_email

    # Check if medication requires physical prescription
    is_controlled = False
    if document_type == "receita" and medication_note:
        controlled = await get_controlled_medications()
        med_lower = medication_note.lower()
        if any(med in med_lower for med in controlled):
            is_controlled = True

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    patient_age = state.get("patient_age")
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    client = await get_supabase()

    # Fetch doctor email from doctors table (agenda_id = email)
    doctor_email = ""
    if doctor_id:
        result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        doctor_email = result.data.get("agenda_id", "") if result.data else ""

    await client.from_("documents").insert({
        "content": f"Solicitação de {document_type}",
        "metadata": {
            "type": document_type,
            "patient_name": patient_name,
            "patient_email": patient_email,
            "doctor_id": doctor_id,
            "phone": phone,
        },
    }).execute()

    await log_event("document_requested", phone, {
        "document_type": document_type,
        "patient_name": patient_name,
    })

    # Register in spreadsheet and notify doctor — fire-and-forget
    import logging as _log
    _doc_logger = _log.getLogger(__name__)
    _doc_logger.warning("DOC_SHEETS_ATTEMPT patient=%s type=%s", patient_name, document_type)
    try:
        await append_document_request(patient_name, patient_age, phone, patient_email, document_type, medication_note)
        _doc_logger.warning("DOC_SHEETS_OK patient=%s", patient_name)
    except Exception:
        _doc_logger.exception("DOC_SHEETS_FAILED patient=%s type=%s", patient_name, document_type)

    try:
        await send_document_request_email(doctor_key, doctor_email, patient_name, patient_age, phone, patient_email, document_type)
    except Exception:
        pass

    doc_labels = {
        "nota_fiscal": "Nota Fiscal", "laudo": "Laudo", "exame": "Exame",
        "relatorio": "Relatório", "receita": "Receita", "declaracao": "Declaração",
    }
    doc_label = doc_labels.get(document_type, document_type)
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
    phone_clean = phone.replace("@s.whatsapp.net", "")
    notify_msg = (
        f"📄 Solicitação de {doc_label}\n"
        f"Paciente: {patient_name}\n"
        f"Médico(a): {doctor_label}\n"
        f"E-mail: {patient_email}\n"
        f"WhatsApp: {phone_clean}"
    )
    if medication_note:
        notify_msg += f"\nMedicação: {medication_note}"
    if is_controlled:
        notify_msg += "\n\n⚠️ RECEITA FÍSICA — o paciente deverá retirar presencialmente na clínica."
    asyncio.create_task(_notify_clinic(notify_msg))

    if is_controlled:
        return (
            "Solicitação registrada! ✅\n"
            "O medicamento solicitado requer receita física. Assim que estiver disponível, "
            "nossa atendente entrará em contato para informar sobre a retirada presencial na clínica."
        )

    return (
        f"Solicitação de {document_type} registrada com sucesso! ✅\n"
        f"Já encaminhamos para o setor responsável e em breve será enviado para você."
    )


@tool
async def confirm_attendance(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """
    Confirma a presença do paciente na consulta agendada.
    Chame este tool quando o paciente confirmar que comparecerá à consulta
    (ex: em resposta a um lembrete). Não chame se o paciente não confirmou explicitamente.
    """
    client = await get_supabase()
    await client.from_("appointments").update({
        "confirmed_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    await log_event("appointment_confirmed", config["configurable"]["phone"], {
        "appointment_id": appointment_id,
    })

    return "Presença confirmada! ✅"


@tool
async def register_payment(
    amount: str,
    drive_link: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    patient_name_override: str = "",
    image_description: str = "",
) -> str:
    """
    Registra um comprovante de pagamento PIX na planilha.
    Chame quando o paciente enviar imagem de comprovante — ela aparecerá no histórico como
    "[imagem]: descrição... [drive_link:URL]".
    amount: valor pago extraído da descrição (ex: "100,00"). Use "?" se não identificado.
    drive_link: URL extraída da tag [drive_link:URL] na descrição. Passe "" se não houver.
    image_description: texto completo da descrição da imagem.
    patient_name_override: use quando este número não tem agendamento e o remetente informou
      o nome do paciente — busca pelo nome no cadastro e envia confirmação ao número original do paciente.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    from app.google_sheets import append_payment_receipt

    phone = config["configurable"]["phone"]
    client = await get_supabase()

    # ── Validate PIX recipient key ────────────────────────────────────────────
    _PIX_KEY = os.getenv("PIX_KEY", "42006848000178")
    _PIX_VARIANTS = {
        _PIX_KEY,
        _PIX_KEY.replace(".", "").replace("/", "").replace("-", ""),  # digits only
    }
    if image_description:
        desc_lower = image_description.lower().replace(".", "").replace("/", "").replace("-", "")
        if not any(v in desc_lower for v in _PIX_VARIANTS):
            return (
                "⚠️ A chave PIX do destinatário no comprovante não corresponde à chave da Psiquê. "
                "Por favor, verifique se o pagamento foi feito para a chave correta: "
                f"{_PIX_KEY}. Caso tenha pago para outra chave, entre em contato com a clínica para resolver."
            )

    # ── Resolve patient ────────────────────────────────────────────────────────
    is_third_party = False
    patient_phone = phone
    user_id = None
    doctor_key = state.get("preferred_doctor", "")

    if patient_name_override.strip():
        # Third-party sender: search user by patient_name
        search_name = patient_name_override.strip()
        user_result = await client.from_("users").select(
            "id, number, patient_name, name, doctor_id"
        ).ilike("patient_name", f"%{search_name}%").limit(5).execute()

        if not user_result.data:
            return (
                f"Não encontrei nenhum paciente com o nome '{search_name}'. "
                "Pode confirmar o nome completo?"
            )

        matched = user_result.data[0]
        patient_name = matched.get("patient_name") or matched.get("name", "Paciente")
        patient_phone = matched["number"] + "@s.whatsapp.net"
        user_id = matched["id"]
        doctor_key = DOCTOR_NAMES.get(matched.get("doctor_id", ""), "")
        is_third_party = True
    else:
        all_users = await get_users_by_phone(phone)
        if not all_users:
            return "Para qual paciente é este comprovante? Por favor, informe o nome completo."

        # Find which patients have a scheduled appointment
        users_with_appt = []
        for u in all_users:
            appt_check = await client.from_("appointments").select("appointment_id").eq(
                "user_id", u["id"]
            ).eq("status", "scheduled").limit(1).execute()
            if appt_check.data:
                users_with_appt.append(u)

        if len(users_with_appt) == 0:
            return "Para qual paciente é este comprovante? Por favor, informe o nome completo."
        elif len(users_with_appt) > 1:
            names = ", ".join(
                u.get("patient_name") or u.get("name", "Paciente")
                for u in users_with_appt
            )
            return f"Encontrei mais de um paciente com consulta agendada neste número: {names}. Para qual deles é o comprovante?"
        else:
            user = users_with_appt[0]
            patient_name = user.get("patient_name") or user.get("name", "Paciente")
            user_id = user["id"]
            doctor_key = DOCTOR_NAMES.get(user.get("doctor_id", ""), "")

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")

    # ── Fetch scheduled appointment or try to reactivate canceled one ─────────
    appointment_dt = "—"
    confirmation_msg = "Comprovante recebido e registrado com sucesso! ✅ Sua vaga está garantida."
    appt_id_to_pay: str | None = None

    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, doctor_id"
    ).eq("user_id", user_id).eq("status", "scheduled").order("start_time").limit(1).execute()

    if appt_result.data:
        apt_start = datetime.fromisoformat(appt_result.data[0]["start_time"]).astimezone(TZ)
        appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
        appt_id_to_pay = appt_result.data[0]["appointment_id"]
    else:
        # No scheduled appointment — try to reactivate the most recent canceled one
        canceled_result = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, doctor_id"
        ).eq("user_id", user_id).eq("status", "canceled").order("updated_at", desc=True).limit(1).execute()

        if canceled_result.data:
            canceled_appt = canceled_result.data[0]
            try:
                from app.google_calendar import get_available_slots, create_event
                slot_start   = datetime.fromisoformat(canceled_appt["start_time"]).astimezone(TZ)
                slot_end     = datetime.fromisoformat(canceled_appt["end_time"]).astimezone(TZ)
                slot_minutes = int((slot_end - slot_start).total_seconds() / 60)

                canceled_doctor_id    = canceled_appt.get("doctor_id", "")
                canceled_doctor_key   = {v: k for k, v in DOCTOR_IDS.items()}.get(canceled_doctor_id, "")
                canceled_doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(canceled_doctor_key, "médico(a)")

                doc_result  = await client.from_("doctors").select("agenda_id").eq("doctor_id", canceled_doctor_id).single().execute()
                calendar_id = doc_result.data.get("agenda_id") if doc_result.data else None

                slot_available = False
                if calendar_id:
                    day_str        = slot_start.strftime("%Y-%m-%d")
                    shift          = "manhã" if slot_start.hour < 12 else ("tarde" if slot_start.hour < 18 else "noite")
                    available_slots = await get_available_slots(calendar_id, day_str, shift, slot_minutes, canceled_doctor_key)
                    slot_available  = any(s == slot_start for s, _ in available_slots)

                if slot_available and calendar_id:
                    # Slot still free — recreate event and reactivate
                    new_event_id = await create_event(
                        calendar_id, slot_start, slot_minutes, patient_name,
                        canceled_doctor_label.replace("Dr. ", "").replace("Dra. ", ""),
                    )
                    await client.from_("appointments").update({
                        "status": "scheduled",
                        "paid_at": datetime.now(TZ).isoformat(),
                        "appointment_id": new_event_id,
                        "updated_at": datetime.now(TZ).isoformat(),
                    }).eq("appointment_id", canceled_appt["appointment_id"]).execute()
                    appointment_dt   = slot_start.strftime("%d/%m/%Y %H:%M")
                    appt_id_to_pay   = None  # already paid above
                    confirmation_msg = (
                        f"Comprovante recebido e registrado com sucesso! ✅\n"
                        f"Sua consulta com *{canceled_doctor_label}* no dia *{appointment_dt}* "
                        f"está reagendada e sua vaga está garantida! 🎉"
                    )
                else:
                    # Slot taken — register payment but inform patient
                    appointment_dt   = slot_start.strftime("%d/%m/%Y %H:%M")
                    appt_id_to_pay   = canceled_appt["appointment_id"]
                    confirmation_msg = (
                        f"Comprovante recebido e registrado! ✅\n"
                        f"Infelizmente o horário original ({appointment_dt} com {canceled_doctor_label}) "
                        f"não está mais disponível. Vou verificar os próximos horários disponíveis para você."
                    )
            except Exception:
                _logger.exception("REACTIVATE_CANCELED_APPT FAILED patient=%s", patient_name)
                if canceled_result.data:
                    apt_start      = datetime.fromisoformat(canceled_result.data[0]["start_time"]).astimezone(TZ)
                    appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
                    appt_id_to_pay = canceled_result.data[0]["appointment_id"]

    # ── Rename Drive file ──────────────────────────────────────────────────────
    if drive_link:
        try:
            from app.google_drive import rename_file
            file_id     = drive_link.split("/d/")[1].split("/")[0]
            amount_clean = amount.replace("R$", "").replace(" ", "").strip()
            date_clean  = (
                appointment_dt.split(" ")[0].replace("/", "-")
                if appointment_dt != "—"
                else datetime.now(TZ).strftime("%d-%m-%Y")
            )
            safe_name    = patient_name.replace(" ", "_")
            new_filename = f"{safe_name}_{date_clean}_R${amount_clean}.jpg"
            await rename_file(file_id, new_filename)
        except Exception:
            _logger.exception("DRIVE_RENAME FAILED")

    try:
        await append_payment_receipt(patient_name, patient_phone, doctor_label, appointment_dt, amount, drive_link)
    except Exception:
        _logger.exception("SHEETS_APPEND FAILED patient=%s", patient_name)

    # ── Mark paid_at if not already set during reactivation ───────────────────
    if appt_id_to_pay:
        try:
            await client.from_("appointments").update({
                "paid_at": datetime.now(TZ).isoformat(),
            }).eq("appointment_id", appt_id_to_pay).execute()
        except Exception:
            _logger.exception("PAID_AT UPDATE FAILED patient=%s", patient_name)

    asyncio.create_task(_notify_clinic(
        f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}\nConsulta: {appointment_dt}\nLink: {drive_link}"
    ))

    await log_event("payment_receipt_registered", phone, {
        "patient_name": patient_name,
        "amount": amount,
        "drive_link": drive_link,
    })

    # ── Notify original patient number if third-party sender ──────────────────
    if is_third_party:
        try:
            await send_text(
                patient_phone,
                f"Olá, {patient_name}! 👋 Recebemos o comprovante de pagamento da sua consulta"
                + (f" com {doctor_label}" if doctor_label != "médico(a)" else "")
                + ". Sua vaga está garantida! ✅",
            )
        except Exception:
            _logger.exception("PATIENT_CONFIRM FAILED phone=%s", patient_phone)

    return confirmation_msg


@tool
async def update_preferred_doctor(
    doctor: Literal["julio", "bruna"],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Atualiza o médico preferido do paciente no cadastro.
    Use quando o paciente informar que o médico cadastrado está incorreto ou quando
    ele escolher um médico pela primeira vez.
    """
    phone = config["configurable"]["phone"]
    doctor_id = DOCTOR_IDS.get(doctor)
    await upsert_user(phone, {"doctor_id": doctor_id})
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, doctor)
    await log_event("doctor_updated", phone, {"doctor": doctor})
    return f"Médico atualizado para {doctor_label}! Pode continuar."


# Attendant working hours (weekday → list of (start_h, end_h) ranges)
_ATTENDANT_HOURS: dict[int, list[tuple[int, int]]] = {
    0: [(8, 12), (13, 18)],  # Segunda
    1: [(8, 12), (13, 18)],  # Terça
    2: [(8, 12), (13, 18)],  # Quarta
    3: [(8, 12), (13, 18)],  # Quinta
    4: [(8, 12), (13, 17)],  # Sexta
    # Sábado e Domingo: sem atendimento
}

_ATTENDANT_HOURS_MSG = (
    "Nossa equipe de atendimento funciona de *segunda a quinta*, das 8h às 12h e das 13h às 18h, "
    "e na *sexta*, das 8h às 12h e das 13h às 17h. "
    "Assim que possível, nossa atendente entrará em contato! 🙏"
)


def _is_attendant_available() -> bool:
    """Return True if current time (Recife) is within attendant working hours."""
    now = datetime.now(TZ)
    ranges = _ATTENDANT_HOURS.get(now.weekday(), [])
    current_minutes = now.hour * 60 + now.minute
    return any(sh * 60 <= current_minutes < eh * 60 for sh, eh in ranges)


@tool
async def transfer_to_human(
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Transfere a conversa para um atendente humano quando o bot não consegue ajudar."""
    from app.chatwoot import add_private_note, find_or_create_conversation, set_labels

    phone = config["configurable"]["phone"]

    # Disable bot for this user
    await upsert_user(phone, {"active": False, "deactivated_at": datetime.now(TZ).isoformat()})

    # Resolve conv_id — fall back to Chatwoot API if not in memory cache (e.g. after server restart)
    conv_id = get_conversation_id(phone)
    if conv_id is None:
        try:
            conv_id = await find_or_create_conversation(phone)
        except Exception:
            logger.warning("Could not resolve Chatwoot conversation for %s", phone)

    if conv_id is not None:
        # Add private note with context for the human agent
        patient_name = state.get("patient_name") or state.get("user_name") or "Não informado"
        doctor = state.get("preferred_doctor", "")
        doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "Não informado")
        number = phone.replace("@s.whatsapp.net", "")
        note_lines = [
            "📋 *Transferido pelo bot*",
            f"👤 Paciente: {patient_name}",
            f"📞 Número: {number}",
            f"🩺 Médico: {doctor_label}",
        ]
        if reason:
            note_lines.append(f"💬 Motivo: {reason}")
        try:
            await add_private_note(conv_id, "\n".join(note_lines))
        except Exception:
            logger.exception("Failed to add private note to Chatwoot conv %s", conv_id)

        try:
            await unassign_agent_bot(conv_id)
        except Exception:
            logger.exception("Failed to unassign Chatwoot agent bot for conv %s", conv_id)

        try:
            await set_labels(conv_id, add=["eva-inativa"], remove=["eva-ativa"])
        except Exception:
            logger.exception("Failed to update eva labels on Chatwoot conv %s", conv_id)

    await log_event("human_transfer", phone, {"reason": reason})

    if _is_attendant_available():
        return "👤 Vou transferir você para um de nossos atendentes. Um momento, por favor!"
    else:
        return (
            "👤 Vou encaminhar você para um de nossos atendentes!\n\n"
            + _ATTENDANT_HOURS_MSG
        )
