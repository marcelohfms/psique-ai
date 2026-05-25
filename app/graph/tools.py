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

async def _notify_clinic(message: str, phone: str = "", subject: str = "Notificação Eva") -> None:
    """Envia notificação para a clínica por e-mail."""
    from app.email_sender import send_clinic_notification_email
    try:
        await send_clinic_notification_email(subject, message)
    except Exception:
        pass


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
    logger.info(
        "GET_SLOTS_CALL preferred_day=%r preferred_shift=%r duration=%s doctor=%s calendar_id=%s",
        preferred_day, preferred_shift, slot_duration_minutes, doctor, calendar_id,
    )
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
        # Detect whether preferred_day is a weekday name so we can do multi-week
        # search — the same logic used below for specific shifts.
        preferred_day_norm_q = preferred_day.lower().strip()
        weekday_key_q = next(
            (wd for name, wd in _WEEKDAYS_PT.items() if name in preferred_day_norm_q),
            None,
        )
        base_date_q = _parse_day(preferred_day)
        if base_date_q is None:
            return "Não entendi a data. Por favor informe um dia específico (ex: segunda, 19/05, amanhã)."

        # For weekday names: try up to 4 weeks until we find a date with slots.
        # For specific dates: single attempt only.
        max_weeks = 4 if weekday_key_q is not None else 1
        for week_offset in range(max_weeks):
            try_date = base_date_q + timedelta(weeks=week_offset)
            day_of_week = _WEEKDAY_LABELS_PT.get(try_date.weekday(), "")
            date_label = try_date.strftime("%d/%m")
            header = f"{day_of_week}, dia {date_label}" if day_of_week else date_label
            sections = []
            for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
                slots = await _get_slots(
                    calendar_id=calendar_id,
                    preferred_day=try_date.isoformat(),
                    preferred_shift=shift_key,
                    slot_minutes=slot_duration_minutes,
                    doctor_key=doctor,
                )
                logger.info("GET_SLOTS_RESULT date=%s shift=%s slots=%s", try_date, shift_key, [s[0].strftime("%H:%M") for s in slots])
                if slots:
                    times = ", ".join(s[0].strftime("%H:%M") for s in slots)
                    sections.append(f"- {shift_label.capitalize()}: {times}")
            if sections:
                return f"Horários disponíveis para {header}:\n" + "\n".join(sections)
            # No slots this week — try the next occurrence (only for weekday names)
        return f"Não há horários disponíveis para {header}. Deseja tentar outro dia?"

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

    # Reject slots on exception days (e.g. doctor on leave)
    from app.google_calendar import SCHEDULE_EXCEPTIONS, DOCTOR_SCHEDULES
    _exc_map = SCHEDULE_EXCEPTIONS.get(doctor, {})
    _date_key = start.date().isoformat()
    if _date_key in _exc_map:
        _day_wins = _exc_map[_date_key]
        if not _day_wins:
            formatted_blocked = start.strftime("%d/%m/%Y")
            return (
                f"A médica não tem atendimento no dia {formatted_blocked}. "
                "Chame get_available_slots para buscar outro horário disponível."
            )
        # Exception overrides schedule but has windows — validate slot falls in one
        _slot_min = start.hour * 60 + start.minute
        if not any((sh * 60 + sm) <= _slot_min < (eh * 60 + em) for sh, sm, eh, em, _ in _day_wins):
            formatted_blocked = start.strftime("%d/%m/%Y")
            return (
                f"Este horário não está dentro da disponibilidade da médica no dia {formatted_blocked}. "
                "Chame get_available_slots para buscar outro horário disponível."
            )

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
            patient_name_hint = state.get("patient_name") or state.get("user_name", "paciente")
            doctor_hint = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
            slot_hint = start.strftime("%d/%m às %H:%M")
            return (
                "AÇÃO NECESSÁRIA: Este horário (quinta à tarde com o Dr. Júlio) pode ser presencial, "
                "mas a disponibilidade precisa ser confirmada pela atendente antes de agendar. "
                "Use transfer_to_human com o seguinte motivo exato: "
                f"'Confirmar disponibilidade presencial para {patient_name_hint} em {slot_hint} com {doctor_hint}. "
                f"Após confirmar, escreva nota privada: "
                f"Eva, pode agendar {patient_name_hint} para {slot_hint} com {doctor_hint}, modalidade presencial.'"
            )
    else:
        effective_modality = modality if modality in ("online", "presencial") else ""

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        doctor, "médico(a)"
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

    # Determine consultation_type for minor patients with Dr. Júlio.
    # Two signals are combined:
    # 1. state["is_patient"]=True → guardian said the patient is already a clinic patient
    # 2. Patient has prior completed appointments in the DB
    # Either signal being True → "acompanhamento"; neither → "primeira_consulta".
    # This handles the common case where the chatbot is new and has no DB history yet,
    # but the guardian says the child is already a returning patient.
    consultation_type: str | None = None
    if patient_age < 18 and doctor == "julio":
        state_says_returning = bool(state.get("is_patient"))
        prior_completed = False
        if user:
            try:
                prior = await client.from_("appointments") \
                    .select("id") \
                    .eq("user_id", user["id"]) \
                    .eq("status", "completed") \
                    .limit(1) \
                    .execute()
                prior_completed = bool(prior.data)
            except Exception:
                _logger.exception("CONSULTATION_TYPE_CHECK FAILED patient=%s", patient_name)
        consultation_type = "acompanhamento" if (state_says_returning or prior_completed) else "primeira_consulta"

    try:
        await client.from_("appointments").insert({
            "user_id": user["id"] if user else None,
            "doctor_id": DOCTOR_IDS.get(doctor),
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": effective_modality or None,
            "consultation_type": consultation_type,
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
    # Read email from DB in case save_patient_email was just called (state may not reflect it yet)
    patient_email = state.get("patient_email")
    if not patient_email:
        _user_for_email = await get_user_by_phone(phone)
        patient_email = (_user_for_email or {}).get("email") or "não informado"
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

    pix_key = os.environ.get("PIX_KEY", "42006848000178")
    return (
        f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}{session_label}\nID: {event_id}\n\n"
        f"INSTRUÇÃO OBRIGATÓRIA: informe agora ao paciente que a vaga só estará garantida após o pagamento "
        f"da taxa de reserva de R$ 100,00 via PIX ({pix_key}) em até 2 horas. "
        f"Peça que envie o comprovante aqui no chat."
    )


@tool
async def cancel_appointment(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Cancela uma consulta agendada. appointment_id é o Google Calendar event ID."""
    from app.google_calendar import cancel_event

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
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

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    if old_start_time:
        old_dt = datetime.fromisoformat(old_start_time).astimezone(TZ)
        formatted_old = old_dt.strftime("%d/%m/%Y às %H:%M")
    else:
        formatted_old = "horário não disponível"

    await _notify_clinic(
        f"Agendamento cancelado! ❌\n"
        f"Paciente: {patient_name}\n"
        f"Data e horário: {formatted_old}\n"
        f"Médico(a): {doctor_label}",
        phone=phone,
        subject=f"Agendamento cancelado — {patient_name}",
    )

    # Send cancellation email to patient if email is on file
    patient_email = state.get("patient_email")
    if not patient_email:
        _user = await get_user_by_phone(phone)
        patient_email = (_user or {}).get("email") or ""
    if patient_email:
        try:
            from app.email_sender import send_cancellation_email
            contact_name = state.get("user_name") or patient_name
            await send_cancellation_email(contact_name, patient_name, doctor_label, formatted_old, patient_email)
        except Exception:
            logger.exception("CANCELLATION_EMAIL FAILED patient=%s", patient_name)

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

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    try:
        new_start = datetime.fromisoformat(new_slot_datetime).replace(tzinfo=TZ)
    except ValueError:
        return f"Formato de data inválido: {new_slot_datetime}. Use ISO 8601 (ex: 2026-03-19T09:00:00)."

    # Reject slots on exception days (e.g. doctor on leave)
    from app.google_calendar import SCHEDULE_EXCEPTIONS
    _exc_map_r = SCHEDULE_EXCEPTIONS.get(doctor, {})
    _date_key_r = new_start.date().isoformat()
    if _date_key_r in _exc_map_r:
        _day_wins_r = _exc_map_r[_date_key_r]
        if not _day_wins_r:
            return (
                f"O médico não tem atendimento no dia {new_start.strftime('%d/%m/%Y')}. "
                "Chame get_available_slots para buscar outro horário disponível."
            )
        _slot_min_r = new_start.hour * 60 + new_start.minute
        if not any((sh * 60 + sm) <= _slot_min_r < (eh * 60 + em) for sh, sm, eh, em, _ in _day_wins_r):
            return (
                f"Este horário não está dentro da disponibilidade do médico no dia {new_start.strftime('%d/%m/%Y')}. "
                "Chame get_available_slots para buscar outro horário disponível."
            )

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    patient_age = state.get("patient_age") or 99
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)

    # Fetch old start_time before updating
    client = await get_supabase()
    appt_result = await client.from_("appointments").select("start_time").eq("appointment_id", appointment_id).maybe_single().execute()
    old_start_time = appt_result.data.get("start_time") if appt_result.data else None

    # Enforce modality constraints
    from app.google_calendar import get_modality_for_slot
    slot_constraint = get_modality_for_slot(doctor, new_start)
    effective_modality = "online" if slot_constraint == "online" else (modality if modality in ("online", "presencial") else "")

    # Update Google Calendar event (same event_id, new time)
    try:
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
    except Exception as e:
        _logger.error("RESCHEDULE_DEBUG update_event FAILED appt=%s error=%s", appointment_id, e, exc_info=True)
        return (
            f"Não foi possível atualizar o evento no Google Calendar (ID: {appointment_id}). "
            f"Erro: {e}. Verifique se o ID do agendamento está correto e tente novamente."
        )

    # Update DB record
    new_end = new_start + timedelta(minutes=slot_duration_minutes)
    reschedule_update: dict = {
        "start_time": new_start.isoformat(),
        "end_time": new_end.isoformat(),
        "updated_at": datetime.now(TZ).isoformat(),
    }
    if effective_modality:
        reschedule_update["modality"] = effective_modality
    await client.from_("appointments").update(reschedule_update).eq("appointment_id", appointment_id).execute()

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

    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {patient_name}\n"
        f"Horário anterior: {formatted_old}\n"
        f"Novo horário: {formatted_new}\n"
        f"Médico(a): {doctor_label}",
        phone=phone,
        subject=f"Agendamento alterado — {patient_name}",
    )

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
        doctor_key = state.get("preferred_doctor", "")
        doctor_label_doc = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "")
        patient_cpf_doc = state.get("patient_cpf") or ""
        await append_document_request(patient_name, patient_age, phone, patient_email, document_type, medication_note, doctor_name=doctor_label_doc, patient_cpf=patient_cpf_doc)
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
    asyncio.create_task(_notify_clinic(notify_msg, subject=f"Solicitação de {doc_label} — {patient_name}"))

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


def _expected_consultation_amount(doctor_key: str, patient_age: int, consultation_type: str | None, now_dt) -> int:
    """Return the expected full payment amount (with R$50 PIX discount).

    consultation_type: value stored in appointments.consultation_type at booking time.
        'primeira_consulta' → first visit pricing (higher)
        'acompanhamento' or None → follow-up pricing (default for unknown)
    """
    post_june = (now_dt.year, now_dt.month) >= (2026, 6)
    if doctor_key == "bruna":
        base = 700 if post_june else 600
    elif doctor_key == "julio":
        if patient_age >= 18:
            base = 700 if post_june else 600
        elif consultation_type == "primeira_consulta":  # minor first visit
            base = 850 if post_june else 750
        else:  # minor follow-up / acompanhamento (default when field is null)
            base = 750 if post_june else 650
    else:
        base = 700 if post_june else 600
    return base - 50  # R$50 PIX/cash discount


@tool
async def register_payment(
    amount: str,
    drive_link: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    patient_name_override: str = "",
    image_description: str = "",
    is_link: bool = False,
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

        # Find which patients have a scheduled or recently completed appointment (last 15 days)
        _appt_lookback = (datetime.now(TZ) - timedelta(days=15)).isoformat()
        users_with_appt = []
        for u in all_users:
            appt_check = await client.from_("appointments").select("appointment_id").eq(
                "user_id", u["id"]
            ).in_("status", ["scheduled", "completed"]).gte("start_time", _appt_lookback).limit(1).execute()
            if appt_check.data:
                users_with_appt.append(u)

        if len(users_with_appt) == 0:
            # No scheduled appointments found for any patient on this number.
            # If multiple patients are registered, list them and ask which one the payment is for.
            patient_names = [
                u.get("patient_name") or u.get("name", "Paciente")
                for u in all_users
            ]
            if len(patient_names) > 1:
                options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(patient_names))
                return (
                    f"Não encontrei nenhum agendamento ativo para os pacientes deste número. "
                    f"Para qual paciente é o comprovante?\n\n{options}"
                )
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
    confirmation_msg = "Comprovante recebido e registrado com sucesso! ✅"
    appt_id_to_pay: str | None = None
    appt_already_occurred = False  # True when the consultation has already happened

    # Look back up to 15 days to capture completed or recently passed appointments
    # (patients commonly delay payment by several days after the consultation).
    lookback_iso = (datetime.now(TZ) - timedelta(days=15)).isoformat()
    appt_result = await client.from_("appointments").select(
        "appointment_id, start_time, end_time, doctor_id, paid_at, booking_fee_paid_at, status, consultation_type"
    ).eq("user_id", user_id).in_("status", ["scheduled", "completed"]).gte("start_time", lookback_iso).order("start_time", desc=True).limit(1).execute()

    if appt_result.data:
        apt_start = datetime.fromisoformat(appt_result.data[0]["start_time"]).astimezone(TZ)
        appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
        # Guard against duplicate calls: only block if full payment already registered.
        # booking_fee_paid_at alone should NOT block — patient may still owe the remaining saldo.
        if appt_result.data[0].get("paid_at"):
            _logger.warning("REGISTER_PAYMENT duplicate call — already paid patient=%s", patient_name)
            return f"Pagamento de {patient_name} para {appointment_dt} já estava registrado anteriormente. ✅"
        appt_id_to_pay = appt_result.data[0]["appointment_id"]
        # Determine if the consultation has already taken place
        appt_already_occurred = (
            appt_result.data[0].get("status") == "completed"
            or apt_start < datetime.now(TZ)
        )
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
                        "booking_fee_paid_at": datetime.now(TZ).isoformat(),
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

    # ── Classify payment and update DB fields ─────────────────────────────────
    try:
        amount_float = float(amount.replace("R$", "").replace(".", "").replace(",", ".").strip())
    except (ValueError, AttributeError):
        amount_float = 0.0

    now_dt = datetime.now(TZ)
    _age = state.get("patient_age") or 99

    # consultation_type is stored at booking time ('primeira_consulta' or 'acompanhamento').
    # For appointments created before this field existed, it will be None → defaults to
    # acompanhamento pricing (safer/cheaper for the patient).
    _consultation_type = (
        appt_result.data[0].get("consultation_type")
        if appt_result and appt_result.data
        else None
    )
    expected = _expected_consultation_amount(doctor_key, _age, _consultation_type, now_dt)

    # If the booking fee was already paid, the remaining balance to settle is expected - 100.
    # This prevents Eva from treating the saldo payment as "partial" and charging R$ 100 again.
    booking_fee_already_paid = bool(
        appt_result.data and appt_result.data[0].get("booking_fee_paid_at")
    ) if appt_result and appt_result.data else False
    expected_remaining = (expected - 100) if booking_fee_already_paid else expected

    if is_link:
        # Link payment confirmed by attendant — no PIX discount applies
        expected_link = expected + 50  # full price without discount
        payment_type = "Consulta — link"
        if appt_id_to_pay:
            try:
                await client.from_("appointments").update({
                    "paid_at": now_dt.isoformat(),
                    "booking_fee_paid_at": now_dt.isoformat(),
                }).eq("appointment_id", appt_id_to_pay).execute()
            except Exception:
                _logger.exception("PAID_AT UPDATE FAILED patient=%s", patient_name)
        payment_note = f"Valor pago: R$ {amount} — pagamento via link. Consulta QUITADA."
    elif amount_float <= 0:
        payment_type = "?"
        payment_note = "Valor não identificado no comprovante."
    elif abs(amount_float - 100) < 1 and not booking_fee_already_paid:
        # Taxa de reserva (only when not yet paid)
        payment_type = "Taxa de Reserva"
        if appt_id_to_pay:
            try:
                await client.from_("appointments").update({
                    "booking_fee_paid_at": now_dt.isoformat(),
                }).eq("appointment_id", appt_id_to_pay).execute()
            except Exception:
                _logger.exception("BOOKING_FEE UPDATE FAILED patient=%s", patient_name)
        saldo = expected - 100
        payment_note = (
            f"Valor pago: R$ {amount} — taxa de reserva registrada. "
            f"Saldo restante para quitação: R$ {saldo:.0f},00 (com desconto PIX)."
        )
    elif amount_float >= expected_remaining:
        # Full payment or saldo that settles the consultation
        payment_type = "Consulta"
        if appt_id_to_pay:
            try:
                await client.from_("appointments").update({
                    "paid_at": now_dt.isoformat(),
                    "booking_fee_paid_at": now_dt.isoformat(),
                }).eq("appointment_id", appt_id_to_pay).execute()
            except Exception:
                _logger.exception("PAID_AT UPDATE FAILED patient=%s", patient_name)
        payment_note = f"Valor pago: R$ {amount} — consulta QUITADA. Nenhum valor adicional será cobrado."
    else:
        # Partial payment — still owes a balance
        payment_type = "Pagamento Parcial"
        if appt_id_to_pay:
            try:
                await client.from_("appointments").update({
                    "booking_fee_paid_at": now_dt.isoformat(),
                }).eq("appointment_id", appt_id_to_pay).execute()
            except Exception:
                _logger.exception("BOOKING_FEE UPDATE FAILED patient=%s", patient_name)
        saldo = expected_remaining - amount_float
        payment_note = (
            f"Valor pago: R$ {amount}. Consulta ainda NÃO quitada. "
            f"Saldo restante: R$ {saldo:.2f} (valor total com desconto PIX: R$ {expected:.0f},00)."
        )

    # ── Record in Google Sheets ────────────────────────────────────────────────
    try:
        await append_payment_receipt(patient_name, patient_phone, doctor_label, appointment_dt, amount, drive_link, payment_type=payment_type)
    except Exception:
        _logger.exception("SHEETS_APPEND FAILED patient=%s", patient_name)

    await _notify_clinic(
        f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}\nTipo: {payment_type}\nConsulta: {appointment_dt}\nLink: {drive_link}",
        subject=f"Comprovante recebido — {patient_name}",
    )

    await log_event("payment_receipt_registered", phone, {
        "patient_name": patient_name,
        "amount": amount,
        "payment_type": payment_type,
        "drive_link": drive_link,
    })

    # ── Notify original patient number if third-party sender ──────────────────
    if is_third_party:
        try:
            if appt_already_occurred:
                patient_msg = (
                    f"Olá, {patient_name}! 👋 Recebemos o comprovante de pagamento da sua consulta"
                    + (f" com {doctor_label}" if doctor_label != "médico(a)" else "")
                    + ". Obrigado! ✅"
                )
            else:
                patient_msg = (
                    f"Olá, {patient_name}! 👋 Recebemos o comprovante de pagamento da sua consulta"
                    + (f" com {doctor_label}" if doctor_label != "médico(a)" else "")
                    + ". Sua vaga está garantida! ✅"
                )
            await send_text(patient_phone, patient_msg)
        except Exception:
            _logger.exception("PATIENT_CONFIRM FAILED phone=%s", patient_phone)

    # Adjust main confirmation message based on whether the consultation already occurred
    if appt_already_occurred and "garantida" in confirmation_msg:
        confirmation_msg = confirmation_msg.replace(" Sua vaga está garantida.", "").replace("Sua vaga está garantida.", "")

    return (
        f"{confirmation_msg}\n\n"
        f"{payment_note}"
    )


@tool
async def save_patient_email(
    email: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Salva o e-mail do paciente no cadastro e no estado da conversa.
    Use quando o paciente informar o e-mail e ele ainda não estiver registrado.
    Deve ser chamado ANTES de confirm_appointment quando patient_email não estiver registrado.
    """
    phone = config["configurable"]["phone"]
    await upsert_user(phone, {"email": email})
    await log_event("patient_email_saved", phone, {"email": email})
    return f"E-mail {email} registrado com sucesso. Agora pode prosseguir com o agendamento."


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
    "de *segunda a quinta*, das 8h às 12h e das 13h às 18h, "
    "e na *sexta*, das 8h às 12h e das 13h às 17h."
)


def _is_attendant_available() -> bool:
    """Return True if current time (Recife) is within attendant working hours."""
    now = datetime.now(TZ)
    ranges = _ATTENDANT_HOURS.get(now.weekday(), [])
    current_minutes = now.hour * 60 + now.minute
    return any(sh * 60 <= current_minutes < eh * 60 for sh, eh in ranges)


@tool
async def register_refund_request(
    appointment_id: str,
    amount: str,
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Registra uma solicitação de reembolso na tabela de agendamentos e sinaliza para a atendente humana.
    Deve ser chamada quando o paciente solicita reembolso da taxa de reserva (cancelamento com >= 24h de antecedência).
    NÃO registra na planilha ainda — isso só ocorre após a atendente confirmar que o reembolso foi realizado.
    amount: valor a ser reembolsado (ex: '100,00' ou 'R$ 100,00').
    """
    phone = config["configurable"]["phone"]
    client = await get_supabase()

    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    doctor_key = state.get("preferred_doctor", "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")

    # Fetch appointment date
    appt_result = await client.from_("appointments").select("start_time").eq("appointment_id", appointment_id).maybe_single().execute()
    appointment_dt = "—"
    if appt_result.data and appt_result.data.get("start_time"):
        start_dt = datetime.fromisoformat(appt_result.data["start_time"]).astimezone(TZ)
        appointment_dt = start_dt.strftime("%d/%m/%Y às %H:%M")

    # Mark refund_requested_at in DB
    now_iso = datetime.now(TZ).isoformat()
    await client.from_("appointments").update({
        "refund_requested_at": now_iso,
        "updated_at": now_iso,
    }).eq("appointment_id", appointment_id).execute()

    # Register in Solicitações spreadsheet
    from app.google_sheets import append_document_request as _append_doc
    try:
        patient_age = state.get("patient_age")
        patient_email = state.get("patient_email") or ""
        patient_cpf = state.get("patient_cpf") or ""
        await _append_doc(
            patient_name=patient_name,
            patient_age=patient_age,
            phone=phone,
            patient_email=patient_email,
            document_type="Solicitação de Reembolso",
            medication_note=f"Valor: R$ {amount} | Consulta: {appointment_dt} | Motivo: {reason}",
            doctor_name=doctor_label,
            patient_cpf=patient_cpf,
        )
    except Exception:
        logger.exception("Failed to append refund request to Solicitações spreadsheet")

    await log_event("refund_requested", phone, {"appointment_id": appointment_id, "amount": amount, "reason": reason})

    return (
        f"Solicitação de reembolso de R$ {amount} registrada para {patient_name} "
        f"(consulta {appointment_dt}). Aguardando confirmação da atendente para finalizar."
    )


@tool
async def confirm_refund_completed(
    appointment_id: str,
    amount: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Confirma que a atendente realizou o reembolso: registra na planilha de pagamentos,
    marca refund_completed_at na tabela de agendamentos e retorna mensagem de confirmação ao paciente.
    Chamar somente quando a atendente enviar nota privada confirmando que o estorno foi realizado.
    amount: valor reembolsado (ex: '100,00').
    """
    from app.google_sheets import append_refund_request as _append_refund

    phone = config["configurable"]["phone"]
    client = await get_supabase()

    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    doctor_key = state.get("preferred_doctor", "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")

    # Fetch appointment date
    appt_result = await client.from_("appointments").select("start_time").eq("appointment_id", appointment_id).maybe_single().execute()
    appointment_dt = "—"
    if appt_result.data and appt_result.data.get("start_time"):
        start_dt = datetime.fromisoformat(appt_result.data["start_time"]).astimezone(TZ)
        appointment_dt = start_dt.strftime("%d/%m/%Y às %H:%M")

    # Mark refund_completed_at in DB
    now_iso = datetime.now(TZ).isoformat()
    await client.from_("appointments").update({
        "refund_completed_at": now_iso,
        "updated_at": now_iso,
    }).eq("appointment_id", appointment_id).execute()

    # Append to payments spreadsheet
    try:
        await _append_refund(
            patient_name=patient_name,
            phone=phone,
            doctor_name=doctor_label,
            appointment_dt=appointment_dt,
            amount=amount,
            reason="Reembolso confirmado pela atendente",
        )
    except Exception:
        logger.exception("Failed to append refund confirmation to spreadsheet")

    await log_event("refund_completed", phone, {"appointment_id": appointment_id, "amount": amount})

    return f"Reembolso de R$ {amount} confirmado e registrado para {patient_name}."


@tool
async def transfer_to_human(
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Transfere a conversa para um atendente humano quando o bot não consegue ajudar."""
    from app.chatwoot import add_private_note, find_or_create_conversation, set_labels

    # In silent_mode (attendant instruction), never transfer — that would re-disable the bot
    # and create an infinite loop. Return the error so the attendant sees it as a private note.
    if state.get("silent_mode"):
        return f"ERRO EM MODO SILENCIOSO: não foi possível executar a instrução. Motivo: {reason}"

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
        note_lines += [
            "",
            "———",
            "💡 *Para devolver o controle à Eva após resolver:*",
            f"Escreva uma nota privada com a instrução completa. Exemplo:",
            f'_"Eva, pode agendar {patient_name} para DD/MM às HH:MM com {doctor_label}, modalidade online/presencial."_',
        ]
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
            "👤 Vou encaminhar você para um de nossos atendentes, mas no momento estamos *fora do horário de atendimento*.\n\n"
            "Nossa equipe funciona " + _ATTENDANT_HOURS_MSG + "\n\n"
            "Assim que retornarmos, sua mensagem será respondida. Pedimos desculpas pelo transtorno! 🙏"
        )
