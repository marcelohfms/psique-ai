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
from app.database import get_supabase, log_event, upsert_user, get_user_by_phone, get_users_by_phone, _phone_variants, DOCTOR_IDS, DOCTOR_NAMES
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


def _build_registration_block(state: dict, phone: str = "") -> str:
    """Return a formatted registration summary for clinic notification emails."""
    lines = ["\n\n📋 CADASTRO DO PACIENTE:"]

    contact = state.get("user_name") or ""
    patient = state.get("patient_name") or contact
    is_patient = state.get("is_patient")

    if is_patient is False and contact and contact != patient:
        lines.append(f"  Responsável: {contact}")

    lines.append(f"  Nome: {patient or '—'}")
    lines.append(f"  Telefone: {phone.replace('@s.whatsapp.net', '') if phone else '—'}")
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
    "presencial_sob_consulta": "online ou presencial",
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
    IMPORTANTE: Se o paciente informar uma data específica (ex: "dia 17", "17/06", "17 de junho"),
    passe SEMPRE a data completa no formato dd/mm (ex: "17/06") — NUNCA converta para o nome
    do dia da semana. Converter "17/06" para "quarta" causaria busca na semana errada.
    Se o paciente mencionar um MÊS específico (ex: "agosto", "consulta em setembro") junto com
    um dia da semana, inclua o mês em preferred_day (ex: "quinta de agosto") para que a busca
    comece a partir daquele mês — NUNCA responda que "a agenda desse mês ainda não está aberta",
    isso não existe; sempre chame a ferramenta com o mês incluído.
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

    age_exception = state.get("age_exception")

    # Dra. Bruna only attends patients aged 12 or older
    if not age_exception and doctor == "bruna" and (state.get("patient_age") or 99) < 12:
        return (
            "Dra. Bruna atende apenas pacientes a partir de 12 anos. "
            "Este paciente tem menos de 12 anos e precisa ser atendido pelo Dr. Júlio. "
            "Por favor, informe o paciente e pergunte se deseja agendar com o Dr. Júlio."
        )

    # Dr. Júlio only attends patients up to 65 anos
    if not age_exception and doctor == "julio" and (state.get("patient_age") or 0) > 65:
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
            # No 2h blocks found — check if there are 1h slots (non-consecutive case)
            if slot_duration_minutes == 120:
                single_sections = []
                for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
                    slots_1h = await _get_slots(
                        calendar_id=calendar_id,
                        preferred_day=try_date.isoformat(),
                        preferred_shift=shift_key,
                        slot_minutes=60,
                        doctor_key=doctor,
                    )
                    if slots_1h:
                        times = ", ".join(s[0].strftime("%H:%M") for s in slots_1h)
                        single_sections.append(f"- {shift_label.capitalize()}: {times}")
                if single_sections:
                    return (
                        f"Há horários disponíveis em {header}, mas não em bloco de 2 horas seguidas:\n"
                        + "\n".join(single_sections)
                        + "\nInforme o paciente que não há 2 horas consecutivas disponíveis neste dia. "
                        "Pergunte se prefere verificar outro dia com 2 horas consecutivas disponíveis."
                    )
            # No slots at all this week — try the next occurrence (only for weekday names)
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
            # No 2h blocks found — check if there are 1h slots (non-consecutive case)
            if slot_duration_minutes == 120:
                slots_1h = await _get_slots(
                    calendar_id=calendar_id,
                    preferred_day=try_date.isoformat(),
                    preferred_shift=preferred_shift,
                    slot_minutes=60,
                    doctor_key=doctor,
                )
                if slots_1h:
                    date_str = try_date.strftime("%d/%m")
                    return (
                        f"Não há bloco de 2 horas seguidas disponível para {day_label}, dia {date_str} "
                        f"({preferred_shift}). "
                        "Informe o paciente e pergunte se prefere verificar outro dia com "
                        "2 horas consecutivas disponíveis."
                    )
            # No slots at all this week — silently try the next one

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
        # No 2h blocks — check if there are 1h slots (non-consecutive case)
        if slot_duration_minutes == 120:
            slots_1h = await _get_slots(
                calendar_id=calendar_id,
                preferred_day=preferred_day,
                preferred_shift=preferred_shift,
                slot_minutes=60,
                doctor_key=doctor,
            )
            if slots_1h:
                date_label = target_date.strftime("%d/%m") if target_date else preferred_day
                return (
                    f"Não há bloco de 2 horas seguidas disponível em {date_label} ({preferred_shift}). "
                    "Informe o paciente e pergunte se prefere verificar outro dia com "
                    "2 horas consecutivas disponíveis."
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
    force_encaixe: bool = False,
    patient_name_override: str = "",
) -> str:
    """
    Confirma e cria o agendamento no Google Calendar.
    slot_datetime deve estar no formato ISO 8601 em HORÁRIO LOCAL DE RECIFE (UTC-3),
    exatamente como exibido ao paciente — ex: se o slot é 08:00, passe '2026-03-19T08:00:00'.
    NUNCA converta para UTC antes de passar — a conversão é feita internamente pela tool.
    session_note: use para identificar sessões separadas de menor de idade,
      ex: '1ª hora — responsáveis' ou '2ª hora — paciente'.
      Deixe vazio para consultas normais ou consultas de 2h em bloco único.
    modality: modalidade de atendimento — "online" ou "presencial".
      Para slots marcados como "apenas online" na listagem, passe "online".
      Para slots com escolha livre, passe o que o paciente escolheu.
      Para slots "presencial requer confirmação": se o paciente escolheu presencial,
      use transfer_to_human antes de chamar confirm_appointment.
    force_encaixe: quando True, ignora verificações de bloqueio de agenda e conflitos
      de horário — use SOMENTE quando a atendente solicitar um encaixe explicitamente.
    patient_name_override: quando a atendente mencionar um nome de paciente diferente
      do que está no estado da conversa (ex: contato tem múltiplos pacientes),
      passe o nome aqui para garantir que o agendamento seja feito para a pessoa correta.
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

    # force_encaixe is only allowed when the request comes from a human attendant
    # (silent_mode=True). Reject any attempt by the patient flow to use it.
    if force_encaixe and not state.get("silent_mode"):
        force_encaixe = False
        _logger.warning("confirm_appointment: force_encaixe=True rejected — not in silent_mode (attendant instruction)")

    # Reject slots outside the doctor's schedule — skipped for encaixe
    if not force_encaixe:
        from app.google_calendar import SCHEDULE_EXCEPTIONS, DOCTOR_SCHEDULES
        _exc_map = SCHEDULE_EXCEPTIONS.get(doctor, {})
        _date_key = start.date().isoformat()
        _slot_min = start.hour * 60 + start.minute
        _doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")

        if _date_key in _exc_map:
            # Exception day: empty list = blocked; non-empty = use those windows
            _day_wins = _exc_map[_date_key]
            if not _day_wins:
                formatted_blocked = start.strftime("%d/%m/%Y")
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"{_doctor_label} não tem atendimento no dia {formatted_blocked}. "
                    "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
                )
            # Exception overrides schedule but has windows — validate slot falls in one
            if not any((sh * 60 + sm) <= _slot_min < (eh * 60 + em) for sh, sm, eh, em, _ in _day_wins):
                formatted_blocked = start.strftime("%d/%m/%Y")
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"Este horário não está dentro da disponibilidade de {_doctor_label} no dia {formatted_blocked}. "
                    "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
                )
        else:
            # Regular day: check weekday is in DOCTOR_SCHEDULES and slot falls in a window
            _weekday = start.weekday()
            _day_wins = DOCTOR_SCHEDULES.get(doctor, {}).get(_weekday)
            if _day_wins is None:
                # Doctor does not work on this weekday at all
                _day_name = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
                             3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"}.get(_weekday, "neste dia")
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"{_doctor_label} não atende {_day_name}. "
                    "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
                )
            # Weekday exists — validate slot falls within one of the day's windows
            if not any((sh * 60 + sm) <= _slot_min < (eh * 60 + em) for sh, sm, eh, em, _ in _day_wins):
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"Este horário ({start.strftime('%H:%M')}) está fora da grade de atendimento de {_doctor_label}. "
                    "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
                )

    # Double-check slot is still free before booking — skipped for encaixe
    if not force_encaixe:
        # Guard 0: block if patient already has a future scheduled appointment (different slot).
        # Forces Eva to use mark_reschedule_in_progress → reschedule_appointment instead.
        try:
            _supabase = await get_supabase()
            _phone = config["configurable"]["phone"]
            _phone_clean = _phone.replace("@s.whatsapp.net", "")
            from datetime import timezone as _tz
            _now_iso = datetime.now(_tz.utc).isoformat()

            # Resolve patient_ids via contacts → patient_contacts
            _contact_r = await _supabase.from_("contacts").select("id").eq("phone", _phone_clean).execute()
            if _contact_r.data:
                _contact_id = _contact_r.data[0]["id"]
                _pc_r = await _supabase.from_("patient_contacts").select("patient_id").eq("contact_id", _contact_id).execute()
                _patient_ids = [row["patient_id"] for row in (_pc_r.data or [])]
                if _patient_ids:
                    _future_r = await _supabase.from_("appointments").select("appointment_id, start_time").in_("patient_id", _patient_ids).eq("status", "scheduled").gte("start_time", _now_iso).execute()
                    _other_appts = [a for a in (_future_r.data or []) if a["start_time"] != start.isoformat()]
                    if _other_appts:
                        from zoneinfo import ZoneInfo as _ZI
                        _TZ = _ZI("America/Recife")
                        _existing_dates = ", ".join(
                            datetime.fromisoformat(a["start_time"]).astimezone(_TZ).strftime("%d/%m/%Y às %H:%M")
                            + f" (ID: {a['appointment_id']})"
                            for a in _other_appts
                        )
                        _logger.warning("confirm_appointment: patient already has scheduled appt(s) — blocking phone=%s", _phone_clean)
                        return (
                            f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                            f"O paciente já tem consulta(s) agendada(s): {_existing_dates}. "
                            "NÃO crie um novo agendamento. OBRIGATÓRIO: chame imediatamente "
                            "mark_reschedule_in_progress com o appointment_id da consulta existente, "
                            "depois get_available_slots, depois reschedule_appointment. "
                            "Nunca retorne erro ao paciente por causa disso."
                        )
        except Exception:
            pass  # Non-fatal — proceed

        # Guard 1: check Supabase for an existing scheduled appointment for this patient
        # at the same time — catches race conditions where two messages trigger confirm_appointment
        # simultaneously before either Calendar event is visible.
        try:
            _supabase = await get_supabase()
            _phone = config["configurable"]["phone"]
            _pids = [u["id"] for u in await get_users_by_phone(_phone)]
            if _pids:
                _uid = ",".join(_pids)  # apenas para log
                _slot_end_check = start + timedelta(minutes=slot_duration_minutes)
                _dup = await _supabase.from_("appointments").select("appointment_id").in_("patient_id", _pids).eq("status", "scheduled").eq("start_time", start.isoformat()).execute()
                if _dup.data:
                    _logger.warning("confirm_appointment: duplicate guard fired for user=%s slot=%s", _uid, start.isoformat())
                    return (
                        f"A consulta das {start.strftime('%H:%M')} do dia {start.strftime('%d/%m/%Y')} "
                        "já está registrada. Não é necessário confirmar novamente."
                    )
        except Exception:
            pass  # Non-fatal — proceed to Calendar check

        # Guard 2: check Google Calendar for conflicts
        from app.google_calendar import _get_busy, _credentials
        from googleapiclient.discovery import build as _build
        slot_end_check = start + timedelta(minutes=slot_duration_minutes)
        try:
            _creds = _credentials()
            _service = _build("calendar", "v3", credentials=_creds)
            loop = asyncio.get_running_loop()
            busy = await loop.run_in_executor(None, _get_busy, _service, calendar_id, start, slot_end_check)
            if busy:
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"Este horário ({start.strftime('%d/%m/%Y às %H:%M')}) acabou de ser ocupado. "
                    "Avise o paciente com empatia que o horário foi preenchido e chame get_available_slots novamente para buscar outro horário disponível."
                )
        except Exception:
            pass  # If check fails, proceed anyway — better to double-book than block

    # Enforce modality constraints from schedule
    from app.google_calendar import get_modality_for_slot
    slot_constraint = get_modality_for_slot(doctor, start)

    # Patient-level restriction overrides everything (except it cannot enable presencial on online-only slots)
    restriction = state.get("modality_restriction")
    if restriction in ("online", "presencial"):
        # If slot is online-only, restriction "presencial" cannot override it
        effective_modality = "online" if slot_constraint == "online" else restriction
    elif slot_constraint == "online":
        effective_modality = "online"
    else:
        effective_modality = modality if modality in ("online", "presencial") else ""

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        doctor, "médico(a)"
    )
    patient_name = patient_name_override.strip() or state.get("patient_name") or state.get("user_name") or "Paciente"
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
            patient_email=state.get("patient_email") or "",
            patient_number=config["configurable"]["phone"],
        )
    except Exception as e:
        _logger.error("CONFIRM_DEBUG create_event FAILED: %s", e, exc_info=True)
        return f"Erro ao criar evento no Google Calendar: {e}"

    _weekday_name = _WEEKDAY_LABELS_PT.get(start.weekday(), "")
    formatted = f"{_weekday_name}, {start.strftime('%d/%m/%Y às %H:%M')}" if _weekday_name else start.strftime("%d/%m/%Y às %H:%M")
    phone = config["configurable"]["phone"]

    # Persist to appointments table; roll back calendar event on failure
    end = start + timedelta(minutes=slot_duration_minutes)
    client = await get_supabase()

    # When the contact has multiple patients, match by patient_name to get the correct user_id.
    # get_user_by_phone returns an arbitrary record — wrong when contact has e.g. parent + child.
    all_users = await get_users_by_phone(phone)
    user = None
    if len(all_users) > 1:
        _target = patient_name.strip().lower()
        for _u in all_users:
            _pname = (_u.get("patient_name") or _u.get("name") or "").strip().lower()
            if _pname == _target:
                user = _u
                break
        if user is None:
            # Fallback: partial match
            for _u in all_users:
                _pname = (_u.get("patient_name") or _u.get("name") or "").strip().lower()
                if _target and _target in _pname:
                    user = _u
                    break
    if user is None:
        user = await get_user_by_phone(phone)

    # Determine consultation_type for minor patients with Dr. Júlio.
    # Two signals are combined:
    # 1. state["is_returning_patient"]=True → guardian said the child is already a patient
    # 2. Patient has prior completed appointments in the DB (excluding split-session slots)
    # Either signal being True → "acompanhamento"; neither → "primeira_consulta".
    #
    # EXCEPTION — split primeira_consulta (session_note set, e.g. "1ª hora — responsáveis"):
    # Skip the prior_completed check entirely. When the 2nd split slot is booked after the
    # 1st slot has already been completed, the prior_completed check would wrongly tag it as
    # "acompanhamento", breaking the linked-payment logic in register_payment.
    consultation_type: str | None = None
    if patient_age < 18 and doctor == "julio":
        state_says_returning = bool(state.get("is_returning_patient"))
        _is_split_slot = bool(session_note)  # any session_note means it's a split primeira_consulta slot
        prior_completed = False
        if user and not _is_split_slot:
            try:
                prior = await client.from_("appointments") \
                    .select("id") \
                    .eq("patient_id", user["id"]) \
                    .eq("status", "completed") \
                    .limit(1) \
                    .execute()
                prior_completed = bool(prior.data)
            except Exception:
                _logger.exception("CONSULTATION_TYPE_CHECK FAILED patient=%s", patient_name)
        consultation_type = "acompanhamento" if (state_says_returning or prior_completed) else "primeira_consulta"

    _bfw = bool((user or {}).get("booking_fee_waived", False))
    _bfp_at = datetime.now(TZ).isoformat() if _bfw else None
    try:
        await client.from_("appointments").insert({
            "patient_id": user["id"] if user else None,
            "contact_id": user.get("_contact_id") if user else None,
            "doctor_id": DOCTOR_IDS.get(doctor),
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": effective_modality or None,
            "consultation_type": consultation_type,
            "booking_fee_waived": _bfw,
            "booking_fee_paid_at": _bfp_at,
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
    registration_block = _build_registration_block(state, phone=phone)
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

    from app.graph.prompts import get_pix_key
    pix_key = get_pix_key()
    _custom_price_ret = (user or {}).get("custom_price")
    _prefix = "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
    if _custom_price_ret == 0:
        return (
            f"{_prefix}AGENDAMENTO_CORTESIA\n"
            f"{doctor_label} — {formatted}{session_label}\nID: {event_id}"
        )
    elif _bfw:
        return (
            f"{_prefix}AGENDAMENTO_TAXA_DISPENSADA\n"
            f"{doctor_label} — {formatted}{session_label}\nID: {event_id}"
        )
    else:
        return (
            f"{_prefix}AGENDAMENTO_OK\n"
            f"{doctor_label} — {formatted}{session_label}\nID: {event_id}"
        )


@tool
async def cancel_appointment(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    preserve_fee: bool = False,
) -> str:
    """Cancela uma consulta agendada. appointment_id é o Google Calendar event ID.
    preserve_fee=True: libera o slot mas mantém a taxa de reserva para uso em remarcação futura
    (status → pending_reschedule). Use quando o cancelamento ocorre dentro do prazo permitido
    e a taxa já foi paga. preserve_fee=False (padrão): cancelamento definitivo (status → canceled).
    """
    from app.google_calendar import cancel_event

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    # Fetch appointment data before canceling for the notification
    client = await get_supabase()
    appt_result = await client.from_("appointments").select("start_time, booking_fee_paid_at").eq("appointment_id", appointment_id).maybe_single().execute()
    old_start_time = (appt_result.data or {}).get("start_time")
    fee_was_paid = bool((appt_result.data or {}).get("booking_fee_paid_at"))

    # Cancel in Google Calendar (frees the slot in both cases)
    await cancel_event(calendar_id, appointment_id)

    # Update status in DB
    new_status = "pending_reschedule" if (preserve_fee and fee_was_paid) else "canceled"
    await client.from_("appointments").update({
        "status": new_status,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    phone = config["configurable"]["phone"]
    await log_event("appointment_canceled", phone, {"appointment_id": appointment_id, "preserve_fee": preserve_fee})

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
    if old_start_time:
        old_dt = datetime.fromisoformat(old_start_time).astimezone(TZ)
        formatted_old = old_dt.strftime("%d/%m/%Y às %H:%M")
    else:
        formatted_old = "horário não disponível"

    if new_status == "pending_reschedule":
        await _notify_clinic(
            f"Consulta liberada para remarcação 🔄\n"
            f"Paciente: {patient_name}\n"
            f"Data e horário liberados: {formatted_old}\n"
            f"Médico(a): {doctor_label}\n"
            f"Taxa de reserva preservada para nova data.",
            phone=phone,
            subject=f"Consulta liberada para remarcação — {patient_name}",
        )
        return "FEE_PRESERVED\nSlot liberado e taxa de reserva preservada para remarcação futura. ✅"
    else:
        await _notify_clinic(
            f"Agendamento cancelado! ❌\n"
            f"Paciente: {patient_name}\n"
            f"Data e horário: {formatted_old}\n"
            f"Médico(a): {doctor_label}",
            phone=phone,
            subject=f"Agendamento cancelado — {patient_name}",
        )
        return "Consulta cancelada com sucesso. ✅"


@tool
async def mark_reschedule_in_progress(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Marca que um reagendamento está em andamento para esta consulta.
    Chame ANTES de get_available_slots quando o paciente pedir para remarcar uma consulta existente.
    Isso registra o timestamp de início para que o sistema possa liberar o slot automaticamente
    caso o paciente não confirme o novo horário em 1 hora.
    """
    client = await get_supabase()
    phone = config["configurable"]["phone"]
    now = datetime.now(TZ)

    # Valida que o appointment pertence a este paciente
    users = await get_users_by_phone(phone)
    user_ids = [u["id"] for u in users]
    appt = await client.from_("appointments").select(
        "appointment_id, status, patient_id, start_time, booking_fee_paid_at, booking_fee_waived"
    ).eq("appointment_id", appointment_id).maybe_single().execute()

    if not appt.data or appt.data.get("patient_id") not in user_ids:
        return "ID de agendamento inválido para este paciente."

    if appt.data.get("status") not in ("scheduled", "pending_reschedule"):
        return "Esta consulta não está em status que permita reagendamento."

    # Regra das 24h precede a regra do primeiro reagendamento: mesmo sendo a
    # primeira remarcação do paciente, se já passou o prazo (19h do dia anterior,
    # ou o próprio dia da consulta) e a taxa já foi paga, a taxa é recolhida e uma
    # nova é cobrada — não se aplica o benefício de remarcação gratuita.
    if not state.get("silent_mode") and appt.data.get("start_time"):
        fee_paid = bool(appt.data.get("booking_fee_paid_at") or appt.data.get("booking_fee_waived"))
        appt_start = datetime.fromisoformat(appt.data["start_time"]).astimezone(TZ)
        deadline = (appt_start - timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
        if fee_paid and now >= deadline:
            return (
                "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Este reagendamento está sendo "
                "solicitado fora do prazo (menos de 24h de antecedência, ou no dia da consulta) "
                "e a taxa de reserva já foi paga. NÃO chame mark_reschedule_in_progress/"
                "reschedule_appointment para este caso, mesmo que seja a primeira remarcação do "
                "paciente. Avise o paciente que a taxa anterior será recolhida e uma nova taxa de "
                "reserva de R$ 100,00 será cobrada para a nova data. Em seguida chame "
                "get_available_slots e, ao confirmar o novo horário, chame cancel_appointment "
                "(para esta consulta) e confirm_appointment (para a nova data)."
            )

    # Política de reagendamento: paciente pode reagendar apenas 1x.
    # A partir do 2º reagendamento iniciado pelo paciente, é necessário
    # solicitar uma nova consulta com nova taxa de reserva.
    # Reagendamentos iniciados pela atendente/médico (silent_mode) são isentos.
    if not state.get("silent_mode"):
        phone_clean = phone.replace("@s.whatsapp.net", "")
        count_res = await client.from_("events").select("id", count="exact") \
            .eq("phone", phone_clean).eq("event_type", "appointment_rescheduled").execute()
        patient_reschedule_count = count_res.count or 0
        if patient_reschedule_count >= 1:
            return (
                "POLÍTICA DE REAGENDAMENTO: este paciente já utilizou o reagendamento disponível. "
                "De acordo com nossa política, a taxa de reserva é transferida apenas uma vez. "
                "Para uma nova consulta, é necessário fazer um novo agendamento e pagar uma nova "
                "taxa de reserva. Informe o paciente e oriente-o a solicitar um novo agendamento."
            )

    # Delete the event from Google Calendar since the appointment is being rescheduled
    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    if calendar_id:
        from app.google_calendar import cancel_event
        try:
            await cancel_event(calendar_id, appointment_id)
        except Exception as e:
            # Log but don't fail — the appointment is already marked for rescheduling
            logger.warning("Failed to delete calendar event %s during reschedule: %s", appointment_id, e)

    # Mark the appointment as pending_reschedule so reschedule_appointment knows to create a new event
    await client.from_("appointments").update({
        "reschedule_requested_at": now.isoformat(),
        "status": "pending_reschedule",
    }).eq("appointment_id", appointment_id).execute()

    await log_event("reschedule_requested", phone, {"appointment_id": appointment_id})

    first_reschedule_notice = ""
    if not state.get("silent_mode") and patient_reschedule_count == 0:
        first_reschedule_notice = (
            " IMPORTANTE: informe ao paciente que este é o único reagendamento permitido sem "
            "perda da taxa de reserva. A partir de um segundo reagendamento, será necessário "
            "solicitar uma nova consulta e pagar uma nova taxa de reserva."
        )
    return f"Reagendamento marcado como em andamento. Prossiga com get_available_slots para buscar novos horários.{first_reschedule_notice}"


@tool
async def change_modality(
    appointment_id: str,
    new_modality: Literal["online", "presencial"],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """
    Altera apenas a modalidade (online ou presencial) de uma consulta existente,
    mantendo a mesma data e hora. appointment_id é o Google Calendar event ID.
    """
    from app.google_calendar import update_event

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    # Fetch appointment data
    client = await get_supabase()
    phone = config["configurable"]["phone"]
    appt_result = await client.from_("appointments").select(
        "start_time, end_time, patient_id, modality, patients(name, email)"
    ).eq("appointment_id", appointment_id).maybe_single().execute()

    if not appt_result.data:
        return "Agendamento não encontrado."

    # Validate that this appointment belongs to this phone number
    _phone_clean = phone.replace("@s.whatsapp.net", "")
    _phone_pids = [u["id"] for u in await get_users_by_phone(_phone_clean)]
    _appt_patient_id = appt_result.data.get("patient_id")
    if _appt_patient_id is None or _appt_patient_id not in _phone_pids:
        return "Este agendamento não pertence a este paciente."

    # Check if modality is actually changing
    current_modality = appt_result.data.get("modality")
    if current_modality == new_modality:
        modality_label = "online" if new_modality == "online" else "presencial"
        return f"A consulta já está marcada como {modality_label}. Nenhuma alteração necessária."

    # Get start time and patient info
    start_time_str = appt_result.data.get("start_time")
    if not start_time_str:
        return "Não foi possível obter a data e hora da consulta."

    start = datetime.fromisoformat(start_time_str).replace(tzinfo=TZ)
    slot_duration = 60  # Assume 1 hour by default
    if appt_result.data.get("end_time"):
        end = datetime.fromisoformat(appt_result.data["end_time"]).replace(tzinfo=TZ)
        slot_duration = int((end - start).total_seconds() / 60)

    _appt_patient = appt_result.data.get("patients") or {}
    patient_name = _appt_patient.get("name") or state.get("patient_name") or state.get("user_name") or "Paciente"
    patient_email = _appt_patient.get("email") or state.get("patient_email") or ""

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
    patient_age = state.get("patient_age") or 99
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)

    # Update Google Calendar event
    try:
        await update_event(
            calendar_id=calendar_id,
            event_id=appointment_id,
            new_start=start,
            slot_minutes=slot_duration,
            patient_name=patient_name,
            doctor_name=doctor_label,
            is_minor_first=is_minor_first,
            modality=new_modality,
            patient_email=patient_email,
            patient_number=config["configurable"]["phone"],
        )
    except Exception as e:
        _logger.error("CHANGE_MODALITY update_event FAILED appt=%s error=%s", appointment_id, e, exc_info=True)
        return f"Não foi possível atualizar o evento no Google Calendar. Erro: {e}"

    # Update DB record
    await client.from_("appointments").update({
        "modality": new_modality,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    await log_event("modality_changed", phone, {
        "appointment_id": appointment_id,
        "new_modality": new_modality,
    })

    formatted_date = start.strftime("%d/%m/%Y às %H:%M")
    modality_label = "online" if new_modality == "online" else "presencial"

    await _notify_clinic(
        f"Modalidade alterada! 🔄\n"
        f"Paciente: {patient_name}\n"
        f"Data e horário: {formatted_date}\n"
        f"Nova modalidade: {modality_label}\n"
        f"Médico(a): {doctor_label}",
        phone=phone,
        subject=f"Modalidade alterada — {patient_name}",
    )

    return f"Modalidade alterada com sucesso! ✅\nSua consulta de {formatted_date} agora é {modality_label}."


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
    new_slot_datetime deve estar no formato ISO 8601 em HORÁRIO LOCAL DE RECIFE (UTC-3),
    exatamente como exibido ao paciente — NUNCA converta para UTC antes de passar.
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
                f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                f"O médico não tem atendimento no dia {new_start.strftime('%d/%m/%Y')}. "
                "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
            )
        _slot_min_r = new_start.hour * 60 + new_start.minute
        if not any((sh * 60 + sm) <= _slot_min_r < (eh * 60 + em) for sh, sm, eh, em, _ in _day_wins_r):
            return (
                f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                f"Este horário não está dentro da disponibilidade do médico no dia {new_start.strftime('%d/%m/%Y')}. "
                "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
            )

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
    patient_age = state.get("patient_age") or 99
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)

    # Fetch old start_time and the actual patient name from the appointment's user record.
    # This avoids using the conversation state's patient_name (which may be the guardian/contact,
    # not the actual patient — e.g. when the phone has multiple patients like parent + child).
    client = await get_supabase()
    phone = config["configurable"]["phone"]
    appt_result = await client.from_("appointments").select("start_time, patient_id, patients(name)").eq("appointment_id", appointment_id).maybe_single().execute()

    # Validate that this appointment actually belongs to this phone number
    _phone_clean = phone.replace("@s.whatsapp.net", "")
    _phone_pids = [u["id"] for u in await get_users_by_phone(_phone_clean)]
    if appt_result.data:
        _appt_patient_id = appt_result.data.get("patient_id")
        if _appt_patient_id is None or _appt_patient_id not in _phone_pids:
            logger.error(
                "RESCHEDULE_VALIDATION FAILED: appointment %s does not belong to phone %s (patient %s)",
                appointment_id, _phone_clean, _appt_patient_id or "unknown",
            )
            # Fetch the correct appointment_id for this phone to help the LLM recover
            _correct = await client.from_("appointments").select(
                "appointment_id, start_time"
            ).in_("patient_id", _phone_pids).eq(
                "status", "scheduled"
            ).order("start_time").limit(1).execute()
            if _correct.data:
                _cid = _correct.data[0]["appointment_id"]
                _cdt = datetime.fromisoformat(_correct.data[0]["start_time"]).astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
                return (
                    f"ID de agendamento inválido para este paciente. "
                    f"O agendamento correto é: {_cdt} (ID: {_cid}). "
                    f"Chame reschedule_appointment novamente com o ID correto."
                )
            return "ID de agendamento inválido para este paciente. Verifique o ID correto nas consultas agendadas."

    old_start_time = appt_result.data.get("start_time") if appt_result.data else None
    if appt_result.data:
        _appt_patient = appt_result.data.get("patients") or {}
        patient_name = _appt_patient.get("name") or state.get("patient_name") or state.get("user_name") or "Paciente"
    else:
        patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"

    # Enforce modality constraints
    from app.google_calendar import get_modality_for_slot
    slot_constraint = get_modality_for_slot(doctor, new_start)
    restriction = state.get("modality_restriction")
    if restriction in ("online", "presencial"):
        effective_modality = "online" if slot_constraint == "online" else restriction
    else:
        effective_modality = "online" if slot_constraint == "online" else (modality if modality in ("online", "presencial") else "")

    # Verifica se o appointment está em pending_reschedule (slot já foi liberado no Calendar).
    # Nesse caso cria um novo evento em vez de atualizar o antigo (que foi cancelado).
    appt_status_result = await client.from_("appointments").select("status").eq("appointment_id", appointment_id).maybe_single().execute()
    is_pending_reschedule = (appt_status_result.data or {}).get("status") == "pending_reschedule"

    from app.google_calendar import create_event
    if is_pending_reschedule:
        # Cria novo evento (o antigo já foi removido do Calendar quando o slot foi liberado)
        try:
            new_event_id = await create_event(
                calendar_id=calendar_id,
                start=new_start,
                slot_minutes=slot_duration_minutes,
                patient_name=patient_name,
                doctor_name=doctor_label,
                modality=effective_modality,
                patient_email=state.get("patient_email") or "",
                patient_number=config["configurable"]["phone"],
            )
            # Atualiza o appointment_id no banco com o novo event_id
            await client.from_("appointments").update({"appointment_id": new_event_id}).eq("appointment_id", appointment_id).execute()
            appointment_id = new_event_id
        except Exception as e:
            _logger.error("RESCHEDULE_DEBUG create_event FAILED appt=%s error=%s", appointment_id, e, exc_info=True)
            return f"Não foi possível criar novo evento no Google Calendar. Erro: {e}"
    else:
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
                patient_email=state.get("patient_email") or "",
                patient_number=config["configurable"]["phone"],
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
        "status": "scheduled",
        "updated_at": datetime.now(TZ).isoformat(),
        "reschedule_requested_at": None,
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
    }
    if effective_modality:
        reschedule_update["modality"] = effective_modality
    await client.from_("appointments").update(reschedule_update).eq("appointment_id", appointment_id).execute()

    _weekday_new = _WEEKDAY_LABELS_PT.get(new_start.weekday(), "")
    formatted_new = f"{_weekday_new}, {new_start.strftime('%d/%m/%Y às %H:%M')}" if _weekday_new else new_start.strftime("%d/%m/%Y às %H:%M")
    await log_event("appointment_rescheduled", phone, {
        "appointment_id": appointment_id,
        "new_datetime": new_slot_datetime,
        "initiated_by": "attendant" if state.get("silent_mode") else "patient",
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
    document_type: Literal["nota_fiscal", "recibo", "laudo", "exame", "relatorio", "receita", "declaracao"],
    patient_email: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    medication_note: str = "",
    financial_name: str = "",
    financial_cpf: str = "",
    financial_email: str = "",
) -> str:
    """Registra uma solicitação de documento médico para o paciente.
    patient_email: e-mail informado pelo paciente para recebimento do documento.
    medication_note: obrigatório quando document_type='receita' — medicação(ões) solicitada(s).
    financial_name/financial_cpf/financial_email: obrigatórios quando document_type='nota_fiscal' e o responsável financeiro for diferente do paciente.
    Mapeamento de termos: 'recibo saúde' ou 'recibo para plano de saúde' → nota_fiscal.
    'recibo' simples (de consulta, de pagamento) → recibo.
    """
    import logging as _log
    _log.getLogger(__name__).warning("REQUEST_DOC_CALLED type=%s email=%s", document_type, patient_email)

    # Fall back to state if LLM didn't pass medication_note explicitly
    if not medication_note.strip():
        medication_note = state.get("medication_note") or ""

    if document_type == "receita" and not medication_note.strip():
        return "Qual medicação você precisa na receita?"

    from app.google_sheets import append_document_request, CONTROLLED_MEDICATIONS
    from app.email_sender import send_document_request_email

    # Check if medication requires physical prescription
    is_controlled = False
    if document_type == "receita" and medication_note:
        med_lower = medication_note.lower()
        if any(med in med_lower for med in CONTROLLED_MEDICATIONS):
            is_controlled = True

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or ""
    if not patient_name:
        _u = await get_user_by_phone(phone)
        patient_name = (_u or {}).get("patient_name") or (_u or {}).get("name") or "Paciente"
    patient_age = state.get("patient_age")
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    # Fallback: use state values if not passed explicitly
    if not financial_name:
        financial_name = state.get("financial_name") or ""
    if not financial_cpf:
        financial_cpf = state.get("financial_cpf") or ""
    if not financial_email:
        financial_email = state.get("financial_email") or ""

    # Persist financial data to DB so future requests don't need to ask again
    if document_type == "nota_fiscal" and (financial_name or financial_cpf or financial_email):
        from app.database import upsert_user
        _fin_data: dict = {}
        if financial_name:
            _fin_data["financial_name"] = financial_name
        if financial_cpf:
            _fin_data["financial_cpf"] = financial_cpf
        if financial_email:
            _fin_data["financial_email"] = financial_email
        try:
            await upsert_user(phone, _fin_data, user_id=state.get("user_db_id"))
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("Failed to persist financial data for %s", phone)

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
            "financial_name": financial_name or None,
            "financial_cpf": financial_cpf or None,
            "financial_email": financial_email or None,
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
        await append_document_request(patient_name, patient_age, phone, patient_email, document_type, medication_note, doctor_name=doctor_label_doc, patient_cpf=patient_cpf_doc, financial_name=financial_name, financial_cpf=financial_cpf, financial_email=financial_email)
        _doc_logger.warning("DOC_SHEETS_OK patient=%s", patient_name)
    except Exception:
        _doc_logger.exception("DOC_SHEETS_FAILED patient=%s type=%s", patient_name, document_type)

    try:
        await send_document_request_email(doctor_key, doctor_email, patient_name, patient_age, phone, patient_email, document_type, financial_name=financial_name, financial_cpf=financial_cpf, financial_email=financial_email)
    except Exception:
        pass

    doc_labels = {
        "nota_fiscal": "Nota Fiscal", "recibo": "Recibo", "laudo": "Laudo", "exame": "Exame",
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
    await _notify_clinic(notify_msg, subject=f"Solicitação de {doc_label} — {patient_name}")

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
async def nudge_doctor_document(
    patient_message: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Notifica o médico por e-mail quando o paciente cobra sobre um DOCUMENTO pendente
    (laudo, declaração, atestado, receita) que já foi solicitado anteriormente.
    Chame SOMENTE quando o paciente perguntar sobre o status de um documento já solicitado
    (ex: 'alguma novidade sobre o laudo?', 'já enviaram o atestado?', 'preciso urgente do documento').
    NÃO use para questões clínicas, dúvidas sobre medicação, sintomas ou qualquer outro assunto
    que não seja um documento físico pendente. Para esses casos, oriente o paciente a entrar em
    contato diretamente com o médico pelo e-mail ou telefone da clínica.
    patient_message: texto exato ou resumo do que o paciente disse.
    """
    from app.email_sender import send_document_nudge_email
    from app.database import DOCTOR_IDS

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
    patient_age = state.get("patient_age")
    patient_email = state.get("patient_email") or ""
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    client = await get_supabase()

    # Find most recent pending document for this patient
    phone_clean = phone.replace("@s.whatsapp.net", "")
    docs = await client.from_("documents").select("*").filter(
        "metadata->>phone", "ilike", f"%{phone_clean[-9:]}%"
    ).order("id", desc=True).limit(1).execute()

    document_type = "declaracao"
    requested_at = "data não registrada"
    if docs.data:
        doc = docs.data[0]
        document_type = (doc.get("metadata") or {}).get("type", document_type)
        # Use document id as proxy for creation order — no created_at column
        requested_at = f"solicitação nº {doc['id']}"

    # Fetch doctor email
    doctor_email = ""
    if doctor_id:
        res = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        doctor_email = res.data.get("agenda_id", "") if res.data else ""

    try:
        await send_document_nudge_email(
            doctor_key=doctor_key,
            doctor_email=doctor_email,
            patient_name=patient_name,
            patient_age=patient_age,
            phone=phone_clean,
            patient_email=patient_email,
            document_type=document_type,
            patient_message=patient_message,
            requested_at=requested_at,
        )
    except Exception:
        logger.exception("nudge_doctor_document: email failed phone=%s", phone)

    await log_event("document_nudge_sent", phone_clean, {
        "document_type": document_type,
        "patient_name": patient_name,
        "patient_message": patient_message,
    })

    return "NUDGE_OK"


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

    # Idempotência: primeiro contato a confirmar vence. Quando vários responsáveis
    # (ex.: pai e mãe) recebem o lembrete, o segundo a confirmar não regrava nem
    # loga de novo — apenas recebe a mesma resposta amigável.
    existing = (
        await client.from_("appointments")
        .select("confirmed_at")
        .eq("appointment_id", appointment_id)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    if rows and rows[0].get("confirmed_at"):
        return "Presença confirmada! ✅"

    await client.from_("appointments").update({
        "confirmed_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    await log_event("appointment_confirmed", config["configurable"]["phone"], {
        "appointment_id": appointment_id,
    })

    return "Presença confirmada! ✅"


def _expected_consultation_amount(
    doctor_key: str,
    patient_age: int,
    consultation_type: str | None,
    now_dt,
    price_override: int | None = None,
) -> int:
    """Return the expected full payment amount (with R$50 PIX discount for standard pricing).

    price_override: if set, returns that value directly — no standard formula, no PIX discount.
    consultation_type: value stored in appointments.consultation_type at booking time.
        'primeira_consulta' → first visit pricing (higher)
        'acompanhamento' or None → follow-up pricing (default for unknown)
    """
    if price_override is not None:
        return price_override
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
    payment_method: str = "",
) -> str:
    """
    Registra um comprovante de pagamento PIX na planilha.
    Chame quando o paciente enviar imagem de comprovante — ela aparecerá no histórico como
    "[imagem]: descrição... [drive_link:URL]".
    amount: valor pago extraído da descrição (ex: "100,00"). Use "?" se não identificado.
    drive_link: URL extraída da tag [drive_link:URL] na descrição. Passe "" se não houver.
    payment_method: método de pagamento presencial registrado pela atendente — "cartao_credito",
      "cartao_debito" ou "dinheiro". Usar apenas em pagamentos presenciais sem comprovante de imagem.
    image_description: texto completo da descrição da imagem.
    patient_name_override: use quando este número não tem agendamento e o remetente informou
      o nome do paciente — busca pelo nome no cadastro e envia confirmação ao número original do paciente.
    """
    import logging as _log
    import re as _re
    _logger = _log.getLogger(__name__)

    from app.google_sheets import append_payment_receipt

    phone = config["configurable"]["phone"]
    client = await get_supabase()

    # ── Sanitize / recover drive_link ─────────────────────────────────────────
    # The LLM sometimes passes the full tag "[drive_link:URL]" instead of the
    # bare URL, or passes "" when the tag WAS present but wasn't extracted.
    # Strategy:
    #  1. Strip the [drive_link:...] wrapper if the LLM passed the full tag.
    #  2. If drive_link is still empty, extract from image_description.
    #  3. If still empty, scan the last few conversation messages.
    def _extract_url(text: str) -> str:
        """Return first https:// URL found in text, stripped of any trailing delimiters."""
        m = _re.search(r'https?://[^\s\]]+', text)
        return m.group(0).rstrip(']"\'') if m else ""

    if drive_link:
        # Case: LLM passed "[drive_link:https://...]" or "drive_link:https://..."
        clean = _extract_url(drive_link)
        if clean:
            drive_link = clean
    if not drive_link and image_description:
        drive_link = _extract_url(image_description)
    if not drive_link:
        for _msg in reversed(state.get("messages", [])):
            _content = getattr(_msg, "content", "") or ""
            if "[drive_link:" in _content:
                drive_link = _extract_url(_content)
                if drive_link:
                    break
    _logger.info(
        "REGISTER_PAYMENT start: drive_link=%r amount=%r image_description=%r",
        drive_link, amount, image_description[:120] if image_description else "",
    )

    # ── Resolve patient ────────────────────────────────────────────────────────
    is_third_party = False
    patient_phone = phone
    user_id = None
    doctor_key = state.get("preferred_doctor", "")

    if patient_name_override.strip():
        # Third-party sender: search patient by name
        search_name = patient_name_override.strip()
        user_result = await client.from_("patients").select(
            "id, name, doctor_id"
        ).ilike("name", f"%{search_name}%").limit(5).execute()

        if not user_result.data:
            return (
                f"Não encontrei nenhum paciente com o nome '{search_name}'. "
                "Pode confirmar o nome completo?"
            )

        matched = user_result.data[0]
        patient_name = matched.get("name", "Paciente")
        user_id = matched["id"]
        # Telefone do paciente vem dos contatos (consulta, ou agendamento como fallback).
        from app.patients import get_contacts_for_patient as _gcfp
        _pcontacts = await _gcfp(user_id, "consulta") or await _gcfp(user_id, "agendamento")
        if not _pcontacts:
            return (
                f"Encontrei o paciente '{patient_name}', mas não há contato cadastrado para ele. "
                "Pode confirmar o número de contato?"
            )
        patient_phone = _pcontacts[0]["phone"] + "@s.whatsapp.net"
        doctor_key = DOCTOR_NAMES.get(matched.get("doctor_id", ""), "")
        is_third_party = True
    else:
        # Query appointments directly (source of truth), joining users to get patient data.
        # A phone number may have multiple patients — the appointment tells us which one
        # actually has an open slot, and provides patient_name via the linked user row.
        all_users = await get_users_by_phone(phone)
        if not all_users:
            return "Para qual paciente é este comprovante? Por favor, informe o nome completo."

        # If the contact has multiple patients, always ask — even if only one has a
        # recent appointment. The payment could be for any of them.
        if len(all_users) > 1:
            names = ", ".join(
                u.get("patient_name") or u.get("name", "Paciente") for u in all_users
            )
            return f"Encontrei mais de um paciente neste número: {names}. Para qual deles é o comprovante?"

        user_ids = [u["id"] for u in all_users]
        _appt_lookback = (datetime.now(TZ) - timedelta(days=15)).isoformat()

        appts_result = await client.from_("appointments").select(
            "appointment_id, start_time, doctor_id, status, patients(id, name)"
        ).in_("patient_id", user_ids).in_(
            "status", ["scheduled", "completed"]
        ).gte("start_time", _appt_lookback).order("start_time", desc=True).execute()

        active_appts = appts_result.data or []

        # Deduplicate by patient (keep only most-recent appointment per patient)
        seen_users: dict[str, dict] = {}
        for a in active_appts:
            u = a.get("patients") or {}
            uid = u.get("id")
            if uid and uid not in seen_users:
                seen_users[uid] = a

        if not seen_users:
            # ── Caso 1: consulta cancelada recente com data futura e taxa pendente ──
            now_iso = datetime.now(TZ).isoformat()
            canceled_result = await client.from_("appointments").select(
                "appointment_id, start_time, doctor_id, status, patients(id, name, birth_date)"
            ).in_("patient_id", user_ids).eq("status", "canceled").is_(
                "booking_fee_paid_at", "null"
            ).gt("start_time", now_iso).order("updated_at", desc=True).limit(3).execute()

            if canceled_result.data:
                a = canceled_result.data[0]
                u = a.get("patients") or {}
                _pname = u.get("name", "Paciente")
                _dt = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
                _doc = {"d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio", "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna"}.get(a.get("doctor_id", ""), "médico(a)")

                # Check if slot is still free using only Supabase appointments (bot-created).
                # Ignores manual Calendar events added by attendants, which are not tracked here.
                _slot_dt = datetime.fromisoformat(a["start_time"]).astimezone(TZ)
                _slot_end_dt = datetime.fromisoformat(a["end_time"]).astimezone(TZ)
                _conflict = await client.from_("appointments").select("id").eq(
                    "doctor_id", a.get("doctor_id", "")
                ).eq("status", "scheduled").lt("start_time", _slot_end_dt.isoformat()).gt(
                    "end_time", _slot_dt.isoformat()
                ).neq("appointment_id", a["appointment_id"]).limit(1).execute()
                _slot_free = not _conflict.data

                if _slot_free:
                    return (
                        f"CONSULTA_CANCELADA_REATIVAVEL: {_pname} tinha uma consulta cancelada em {_dt} com {_doc} "
                        f"com taxa de reserva pendente. O horário ainda está livre no calendário. "
                        f"appointment_id={a['appointment_id']} user_id={u.get('id')} "
                        f"Confirme com o contato se deseja reativar esta consulta antes de registrar o pagamento."
                    )
                else:
                    return (
                        f"CONSULTA_CANCELADA_SEM_SLOT: {_pname} tinha uma consulta cancelada em {_dt} com {_doc} "
                        f"com taxa de reserva pendente, mas o horário já está ocupado. "
                        f"appointment_id={a['appointment_id']} user_id={u.get('id')} "
                        f"Confirme com o contato se quer agendar uma nova data. "
                        f"Se confirmar, mude o status para pending_reschedule."
                    )

            return "Para qual paciente é este comprovante? Por favor, informe o nome completo."

        if len(seen_users) > 1:
            names = ", ".join(
                (a.get("patients") or {}).get("name", "Paciente")
                for a in seen_users.values()
            )
            return f"Encontrei mais de um paciente com consulta agendada neste número: {names}. Para qual deles é o comprovante?"

        appt_ref = next(iter(seen_users.values()))
        appt_user = appt_ref.get("patients") or {}
        patient_name = appt_user.get("name", "Paciente")
        user_id = appt_user.get("id")
        # doctor_key from appointment row (will be overridden again below once full appt is fetched)
        doctor_key = DOCTOR_NAMES.get(appt_ref.get("doctor_id", ""), "")

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")

    # ── Fetch scheduled appointment or try to reactivate canceled one ─────────
    appointment_dt = "—"
    confirmation_msg = "Comprovante recebido e registrado com sucesso! ✅"
    appt_id_to_pay: str | None = None
    apt_start: datetime | None = None
    appt_already_occurred = False  # True when the consultation has already happened

    # Appointment resolution order (critical — must follow this exact priority):
    #   1. An active SCHEDULED appointment always wins. It is the one awaiting the
    #      booking fee, so the payment must land on it — never on a canceled slot.
    #   2. If there is NO scheduled appointment, try to reactivate a future canceled
    #      one (patient paid after the slot was auto-canceled).
    #   3. Only then fall back to a completed past appointment (late full payment).
    # Looking at canceled appointments before scheduled ones caused payments to be
    # applied to the wrong (canceled) appointment, wrongly auto-canceling the active one.
    now_iso = datetime.now(TZ).isoformat()
    lookback_iso = (datetime.now(TZ) - timedelta(days=15)).isoformat()
    _appt_fields = (
        "appointment_id, start_time, end_time, doctor_id, paid_at, "
        "booking_fee_paid_at, status, consultation_type, booking_fee_waived"
    )

    # PRIORITY 1: active scheduled appointment.
    scheduled_raw = await client.from_("appointments").select(_appt_fields).eq(
        "patient_id", user_id
    ).eq("status", "scheduled").gte("start_time", lookback_iso).order(
        "start_time", desc=True
    ).limit(1).execute()

    if scheduled_raw.data:
        appt_result_data = scheduled_raw.data
    else:
        # PRIORITY 2: future canceled appointment that can be reactivated.
        future_canceled = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, doctor_id, booking_fee_paid_at, booking_fee_waived"
        ).eq("patient_id", user_id).eq("status", "canceled").eq("booking_fee_waived", False).is_(
            "booking_fee_paid_at", "null"
        ).gte("start_time", now_iso).order("start_time").limit(1).execute()

        if future_canceled.data:
            # Defer to the reactivation branch below by returning no active appointment.
            appt_result_data = []
        else:
            # PRIORITY 3: completed past appointment (late full payment).
            completed_raw = await client.from_("appointments").select(_appt_fields).eq(
                "patient_id", user_id
            ).eq("status", "completed").gte("start_time", lookback_iso).order(
                "start_time", desc=True
            ).limit(1).execute()
            appt_result_data = completed_raw.data

    # Wrap in a simple object so the rest of the function works unchanged
    class _ApptResult:
        def __init__(self, data): self.data = data
    appt_result = _ApptResult(appt_result_data)

    # IDs of all appointments that should be updated together on payment.
    # For split primeira_consulta (two 1h slots), both get paid_at/booking_fee_paid_at at once.
    linked_appt_ids: list[str] = []

    if appt_result.data:
        apt_start = datetime.fromisoformat(appt_result.data[0]["start_time"]).astimezone(TZ)
        appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")
        # Override doctor from the appointment itself — more reliable than user record or state.
        appt_doctor_id = appt_result.data[0].get("doctor_id", "")
        if appt_doctor_id:
            _appt_doctor_key = DOCTOR_NAMES.get(appt_doctor_id, "")
            if _appt_doctor_key:
                doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(_appt_doctor_key, doctor_label)
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
        # For split primeira_consulta, collect all linked appointment IDs so every
        # slot is updated together when payment is registered.
        # Also fetch start_time to use the earliest slot's date for pricing
        # (price reajuste applies from June — a bundle that started in May keeps May pricing).
        if appt_result.data[0].get("consultation_type") == "primeira_consulta":
            linked_res = await client.from_("appointments").select(
                "appointment_id, start_time"
            ).eq("patient_id", user_id).eq("consultation_type", "primeira_consulta").in_(
                "status", ["scheduled", "completed"]
            ).execute()
            linked_appt_ids = [a["appointment_id"] for a in (linked_res.data or [])]
            # Use the earliest slot's date as the pricing reference date
            if linked_res.data:
                earliest_start = min(
                    datetime.fromisoformat(a["start_time"]) for a in linked_res.data
                )
                apt_start = earliest_start.astimezone(TZ)
        if not linked_appt_ids:
            linked_appt_ids = [appt_id_to_pay]
    else:
        # No scheduled appointment — try to reactivate the most recent canceled one
        canceled_result = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, doctor_id, modality"
        ).eq("patient_id", user_id).eq("status", "canceled").order("updated_at", desc=True).limit(1).execute()

        if canceled_result.data:
            canceled_appt = canceled_result.data[0]
            try:
                from app.google_calendar import get_available_slots, create_event
                slot_start   = datetime.fromisoformat(canceled_appt["start_time"]).astimezone(TZ)
                slot_end     = datetime.fromisoformat(canceled_appt["end_time"]).astimezone(TZ)
                slot_minutes = int((slot_end - slot_start).total_seconds() / 60)
                apt_start    = slot_start

                canceled_doctor_id    = canceled_appt.get("doctor_id", "")
                canceled_doctor_key   = {v: k for k, v in DOCTOR_IDS.items()}.get(canceled_doctor_id, "")
                canceled_doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(canceled_doctor_key, "médico(a)")

                doc_result  = await client.from_("doctors").select("agenda_id").eq("doctor_id", canceled_doctor_id).single().execute()
                calendar_id = doc_result.data.get("agenda_id") if doc_result.data else None

                slot_available = False
                if calendar_id:
                    # Check directly for conflicts in the calendar without applying
                    # schedule restrictions — the original booking may have been an
                    # encaixe outside normal hours, and we must honour that.
                    from app.google_calendar import _credentials, _get_busy
                    from googleapiclient.discovery import build as _build
                    _creds = _credentials()
                    _svc   = _build("calendar", "v3", credentials=_creds)
                    loop   = asyncio.get_running_loop()
                    busy_raw = await loop.run_in_executor(
                        None, _get_busy, _svc, calendar_id, slot_start, slot_end
                    )
                    # No bot-created events in this window → slot is free
                    slot_available = len(busy_raw) == 0

                if slot_available and calendar_id:
                    # Slot still free — recreate event and reactivate
                    new_event_id = await create_event(
                        calendar_id, slot_start, slot_minutes, patient_name,
                        canceled_doctor_label.replace("Dr. ", "").replace("Dra. ", ""),
                        modality=canceled_appt.get("modality") or "",
                        patient_email=state.get("patient_email") or "",
                        patient_number=patient_phone,
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
                    # Slot taken — mark booking fee as paid, set pending_reschedule
                    # so the patient can choose a new time without losing payment info.
                    appointment_dt = slot_start.strftime("%d/%m/%Y %H:%M")
                    await client.from_("appointments").update({
                        "status": "pending_reschedule",
                        "booking_fee_paid_at": datetime.now(TZ).isoformat(),
                        "updated_at": datetime.now(TZ).isoformat(),
                    }).eq("appointment_id", canceled_appt["appointment_id"]).execute()
                    appt_id_to_pay = None  # booking fee already registered above
                    confirmation_msg = (
                        f"Comprovante recebido e registrado! ✅\n"
                        f"Infelizmente o horário original ({appointment_dt} com {canceled_doctor_label}) "
                        f"não está mais disponível. Vou verificar os próximos horários disponíveis "
                        f"para remarcar sua consulta — sua taxa de reserva já está registrada e "
                        f"não precisará ser paga novamente. 🙏"
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
            # Support both /d/{id}/... and ?id={id} URL formats
            _fid_match = _re.search(r'/d/([^/?&#\s]+)', drive_link) or \
                         _re.search(r'[?&]id=([^?&#\s]+)', drive_link)
            if not _fid_match:
                raise ValueError(f"Cannot extract file_id from drive_link: {drive_link!r}")
            file_id      = _fid_match.group(1)
            amount_clean = amount.replace("R$", "").replace(" ", "").strip()
            date_clean   = (
                appointment_dt.split(" ")[0].replace("/", "-")
                if appointment_dt != "—"
                else datetime.now(TZ).strftime("%d-%m-%Y")
            )
            safe_name    = patient_name.replace(" ", "_")
            new_filename = f"{safe_name}_{date_clean}_R${amount_clean}.jpg"
            await rename_file(file_id, new_filename)
            _logger.info("DRIVE_RENAME OK file_id=%s new_name=%s", file_id, new_filename)
        except Exception:
            _logger.exception("DRIVE_RENAME FAILED drive_link=%r", drive_link)

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
    # For pricing, use the date of the first appointment in the bundle.
    # A split primeira_consulta that started in May keeps May pricing even if payment
    # arrives in June (after the price reajuste). apt_start was already set to the
    # earliest slot when linked appointments were fetched.
    pricing_dt = apt_start if apt_start else now_dt
    # If the booking fee was already paid, the remaining balance to settle is expected - 100.
    # This prevents Eva from treating the saldo payment as "partial" and charging R$ 100 again.
    booking_fee_already_paid = bool(
        appt_result.data and appt_result.data[0].get("booking_fee_paid_at")
    ) if appt_result and appt_result.data else False

    # booking_fee_waived: the fee was never owed — don't deduct R$100 from expected_remaining
    _appt_bfw = bool(
        appt_result.data[0].get("booking_fee_waived", False)
    ) if appt_result and appt_result.data else False

    # custom_price from patient record (overrides standard formula in _expected_consultation_amount)
    custom_price: int | None = None
    if user_id:
        try:
            _user_cp = await client.from_("patients").select("custom_price").eq(
                "id", user_id
            ).maybe_single().execute()
            custom_price = (_user_cp.data or {}).get("custom_price")
        except Exception:
            pass

    expected = _expected_consultation_amount(
        doctor_key, _age, _consultation_type, pricing_dt, price_override=custom_price
    )

    if _appt_bfw:
        expected_remaining = expected        # booking fee was never owed
    else:
        expected_remaining = (expected - 100) if booking_fee_already_paid else expected

    async def _update_appts(fields: dict) -> None:
        """Apply a payment field update to all linked appointment IDs."""
        ids = linked_appt_ids if linked_appt_ids else ([appt_id_to_pay] if appt_id_to_pay else [])
        for aid in ids:
            try:
                await client.from_("appointments").update(fields).eq("appointment_id", aid).execute()
            except Exception:
                _logger.exception("APPT UPDATE FAILED appt=%s patient=%s", aid, patient_name)

    # ── Courtesy (custom_price == 0) — always QUITADA ────────────────────────
    if custom_price == 0:
        if appt_id_to_pay:
            await _update_appts({
                "paid_at": now_dt.isoformat(),
                "booking_fee_paid_at": now_dt.isoformat(),
            })
        try:
            await append_payment_receipt(
                patient_name, patient_phone, doctor_label, appointment_dt,
                amount, drive_link, payment_type="Consulta", payment_method_override="",
            )
        except Exception:
            _logger.exception("SHEETS_APPEND FAILED patient=%s", patient_name)
        await _notify_clinic(
            f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}"
            f"\nTipo: Consulta (cortesia)\nConsulta: {appointment_dt}\nLink: {drive_link}",
            subject=f"Comprovante recebido — {patient_name}",
        )
        await log_event("payment_receipt_registered", phone, {
            "patient_name": patient_name, "amount": amount,
            "payment_type": "Consulta", "drive_link": drive_link,
        })
        return f"{confirmation_msg}\n\nConsulta QUITADA (cortesia). ✅ Nenhum valor adicional será cobrado."

    _sheets_payment_method: str = ""  # populated below, passed to append_payment_receipt
    if is_link or payment_method:
        # Attendant-confirmed payment (link, presencial cartão/dinheiro) — no PIX discount applies
        _method_labels = {
            "cartao_credito": "Cartão de Crédito",
            "cartao_debito": "Cartão de Débito",
            "dinheiro": "Dinheiro",
        }
        if payment_method:
            _sheets_payment_method = _method_labels.get(payment_method, payment_method)
            payment_note = f"Valor pago: R$ {amount} — {_sheets_payment_method} (presencial). Consulta QUITADA."
        else:
            _sheets_payment_method = "Link"
            payment_note = f"Valor pago: R$ {amount} — pagamento via link. Consulta QUITADA."
        payment_type = "Consulta"
        if appt_id_to_pay:
            await _update_appts({"paid_at": now_dt.isoformat(), "booking_fee_paid_at": now_dt.isoformat()})
    elif amount_float <= 0:
        payment_type = "?"
        payment_note = "Valor não identificado no comprovante."
    elif abs(amount_float - 100) < 1 and not booking_fee_already_paid:
        # Taxa de reserva (only when not yet paid)
        payment_type = "Taxa de Reserva"
        if appt_id_to_pay:
            await _update_appts({"booking_fee_paid_at": now_dt.isoformat()})
        saldo = expected - 100
        payment_note = (
            f"Valor pago: R$ {amount} — taxa de reserva registrada. "
            f"Saldo restante para quitação: R$ {saldo:.0f},00 (com desconto PIX)."
        )
    elif amount_float >= expected_remaining:
        # Full payment or saldo that settles the consultation
        payment_type = "Consulta"
        if appt_id_to_pay:
            await _update_appts({"paid_at": now_dt.isoformat(), "booking_fee_paid_at": now_dt.isoformat()})
        payment_note = f"Valor pago: R$ {amount} — consulta QUITADA. Nenhum valor adicional será cobrado."
    else:
        # Partial payment — still owes a balance
        payment_type = "Pagamento Parcial"
        if appt_id_to_pay:
            await _update_appts({"booking_fee_paid_at": now_dt.isoformat()})
        saldo = expected_remaining - amount_float
        if custom_price is not None:
            payment_note = (
                f"Valor pago: R$ {amount}. Consulta ainda NÃO quitada. "
                f"Saldo restante: R$ {saldo:.2f} (valor especial do paciente: R$ {expected:.0f},00)."
            )
        else:
            payment_note = (
                f"Valor pago: R$ {amount}. Consulta ainda NÃO quitada. "
                f"Saldo restante: R$ {saldo:.2f} (valor total com desconto PIX: R$ {expected:.0f},00)."
            )

    # ── Record in Google Sheets ────────────────────────────────────────────────
    try:
        await append_payment_receipt(patient_name, patient_phone, doctor_label, appointment_dt, amount, drive_link, payment_type=payment_type, payment_method_override=_sheets_payment_method)
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
    # Normalize: strip accents so "júlio" → "julio" in case the LLM adds one
    doctor_normalized = doctor.lower().replace("ú", "u").replace("ü", "u")
    doctor_key = doctor_normalized if doctor_normalized in DOCTOR_IDS else doctor
    doctor_id = DOCTOR_IDS.get(doctor_key)
    if not doctor_id:
        return f"Médico '{doctor}' não reconhecido. Use 'julio' ou 'bruna'."
    await upsert_user(phone, {"doctor_id": doctor_id}, user_id=state.get("user_db_id"))
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, doctor_key)
    await log_event("doctor_updated", phone, {"doctor": doctor_key})
    return f"Médico atualizado para {doctor_label}! Pode continuar."


@tool
async def request_registration_update(
    field: str,
    new_value: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Registra uma solicitação de alteração de dados cadastrais do paciente.
    Use SOMENTE quando o paciente solicitar explicitamente a correção ou atualização
    de um dado já existente (e-mail, CPF, nome, data de nascimento, etc.).
    NÃO use durante o fluxo normal de coleta de dados para agendamento.
    Para e-mail: atualiza o banco imediatamente. Para qualquer campo: notifica a equipe por e-mail.
    """
    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
    now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    phone_clean = phone.replace("@s.whatsapp.net", "")
    field_norm = field.lower().strip()

    # If the field is email, update the DB directly
    applied_directly = False
    if field_norm in ("email", "e-mail"):
        await upsert_user(phone, {"email": new_value}, user_id=state.get("user_db_id"))
        applied_directly = True
    elif "nome" in field_norm and "paciente" in field_norm and state.get("is_patient") is False:
        # Safety net for the "patient_name still unknown/wrong" bug: when the contact
        # is NOT the patient and patient_name is missing or was defaulted to the
        # contact's own name (never a real answer), this isn't a "correction" of an
        # established value — it's filling in data collect_info should have asked
        # for. Apply immediately instead of queueing a manual review.
        _stale = not state.get("patient_name") or state.get("patient_name") == state.get("user_name")
        if _stale:
            await upsert_user(phone, {"patient_name": new_value}, user_id=state.get("user_db_id"))
            applied_directly = True

    # Notify the attendant regardless of field type
    subject = f"Solicitação de alteração cadastral — {patient_name}"
    body = (
        f"Paciente solicitou alteração cadastral.\n\n"
        f"Nome: {patient_name}\n"
        f"Telefone: {phone_clean}\n"
        f"Campo: {field}\n"
        f"Novo valor: {new_value}\n"
        f"Data/hora: {now_str}\n"
        + ("(Aplicado automaticamente ao cadastro)\n" if applied_directly else "")
    )
    await _notify_clinic(body, phone=phone, subject=subject)

    if applied_directly:
        return f"{field} atualizado com sucesso para {new_value}."
    return f"Pedido de alteração de {field} registrado. A equipe irá processar em breve."


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

# Datas de recesso da atendente (formato: date(YYYY, MM, DD))
_ATTENDANT_RECESS_DAYS: list[tuple[int, int, int]] = [
    (2026, 6, 23),  # Recesso São João
    (2026, 6, 24),  # Recesso São João
]

_ATTENDANT_RECESS_MSG: dict[tuple[int, int, int], str] = {
    (2026, 6, 23): "em *recesso de São João* nos dias 23 e 24/06",
    (2026, 6, 24): "em *recesso de São João* nos dias 23 e 24/06",
}


def _get_recess_message(now: datetime) -> str | None:
    """Return a recess message if today is a recess day, otherwise None."""
    key = (now.year, now.month, now.day)
    return _ATTENDANT_RECESS_MSG.get(key)


def _is_attendant_available() -> bool:
    """Return True if current time (Recife) is within attendant working hours."""
    now = datetime.now(TZ)
    if (now.year, now.month, now.day) in _ATTENDANT_RECESS_DAYS:
        return False
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

    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
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

    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
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
        # Add private note with context for the human agent.
        # Always prefer the DB record over state.patient_name, which may contain
        # raw conversation text (e.g. "Ainda não é paciente, mas o nome dele é...").
        patient_name = state.get("patient_name") or state.get("user_name") or "Não informado"
        try:
            from app.database import get_user_by_phone as _get_user_by_phone
            _fb = await _get_user_by_phone(phone)
            if _fb:
                patient_name = _fb.get("patient_name") or _fb.get("name") or patient_name
        except Exception:
            pass
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
        now = datetime.now(TZ)
        recess_msg = _get_recess_message(now)
        if recess_msg:
            return (
                f"👤 Vou encaminhar você para um de nossos atendentes, mas nossa equipe está {recess_msg}.\n\n"
                "Retornaremos na *quarta-feira, 25/06*, no horário normal de atendimento.\n\n"
                "Assim que voltarmos, sua mensagem será respondida. Pedimos desculpas pelo transtorno! 🙏"
            )
        return (
            "👤 Vou encaminhar você para um de nossos atendentes, mas no momento estamos *fora do horário de atendimento*.\n\n"
            "Nossa equipe funciona " + _ATTENDANT_HOURS_MSG + "\n\n"
            "Assim que retornarmos, sua mensagem será respondida. Pedimos desculpas pelo transtorno! 🙏"
        )


@tool
async def consultar_data(data: str) -> str:
    """Retorna o dia da semana e a relação com hoje (hoje/amanhã/em N dias) de uma
    data. Use SEMPRE que precisar mencionar o dia da semana de uma data que NÃO
    esteja no CALENDÁRIO DE REFERÊNCIA do prompt (ou seja, mais de 35 dias à
    frente). Aceita 'dd/mm' ou 'dd/mm/aaaa'. Nunca calcule o dia da semana você
    mesmo — chame esta ferramenta."""
    from app.dates import weekday_pt, relative_label

    today = datetime.now(TZ).date()
    raw = (data or "").strip()

    parsed = None
    # Full date first; then dd/mm with year inference.
    try:
        parsed = datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        try:
            dm = datetime.strptime(raw, "%d/%m")
        except ValueError:
            # Retry with a known leap year so '29/02' parses; only day & month
            # are used below (the real year is chosen by the offset loop).
            try:
                dm = datetime.strptime(f"{raw}/2024", "%d/%m/%Y")
            except ValueError:
                dm = None
        if dm is not None:
            # Find the next year (starting at the current year) in which dd/mm is
            # a valid date on/after today — handles 29/02 and past dates.
            for offset in range(0, 8):
                try:
                    cand = dm.replace(year=today.year + offset).date()
                except ValueError:
                    continue  # e.g. 29/02 on a non-leap year
                if cand >= today:
                    parsed = cand
                    break

    if parsed is None:
        return (
            "Não consegui entender a data. Envie no formato dd/mm ou dd/mm/aaaa "
            "(ex: 15/09 ou 15/09/2026)."
        )

    wd = weekday_pt(parsed)
    article = "um" if wd in ("sábado", "domingo") else "uma"
    delta = (parsed - today).days
    rel_near = relative_label(parsed, today)
    if rel_near:
        rel = rel_near
    elif delta > 0:
        rel = f"em {delta} dias"
    else:
        rel = f"há {abs(delta)} dias"

    return f"{parsed.strftime('%d/%m/%Y')} é {article} {wd} ({rel})."


@tool
async def extend_payment_deadline(
    deadline_iso: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Estende o prazo de pagamento da taxa de reserva quando o paciente pede mais tempo.

    Use quando o paciente disser que vai pagar mais tarde, amanhã, em X horas, etc.
    O lembrete automático será reenviado 2h antes do prazo e o cancelamento ocorrerá
    2h após o lembrete, se não pago.

    deadline_iso: data e hora limite para pagamento em ISO 8601 com fuso (ex: '2026-06-26T10:00:00-03:00').
                  Interprete o pedido do paciente e converta para este formato.
    """
    phone = config["configurable"]["phone"]
    client = await get_supabase()

    # Find the scheduled appointment without booking fee
    from app.database import get_users_by_phone
    users = await get_users_by_phone(phone)
    if not users:
        return "Não encontrei cadastro para este número."

    user_ids = [u["id"] for u in users]
    from datetime import timezone as _tz
    now_iso = datetime.now(_tz.utc).isoformat()

    appt = None
    for uid in user_ids:
        result = await client.from_("appointments").select(
            "appointment_id, start_time"
        ).eq("user_id", uid).eq("status", "scheduled").is_("booking_fee_paid_at", "null").eq("booking_fee_waived", False).gte("start_time", now_iso).order("start_time").limit(1).execute()
        if result.data:
            appt = result.data[0]
            break

    # Also check patient_id path
    if not appt:
        from app.database import get_user_by_phone
        user = await get_user_by_phone(phone)
        if user:
            patient_result = await client.from_("appointments").select(
                "appointment_id, start_time"
            ).eq("patient_id", user.get("patient_id", "")).eq("status", "scheduled").is_("booking_fee_paid_at", "null").eq("booking_fee_waived", False).gte("start_time", now_iso).order("start_time").limit(1).execute()
            if patient_result.data:
                appt = patient_result.data[0]

    if not appt:
        return "Não encontrei consulta agendada com taxa de reserva pendente para este paciente."

    # Parse deadline and compute new created_at (deadline - 2h so reminder fires at deadline)
    try:
        deadline_dt = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return f"Formato de data inválido: {deadline_iso}. Use ISO 8601 (ex: '2026-06-26T10:00:00-03:00')."

    new_created_at = (deadline_dt - timedelta(hours=2)).isoformat()

    await client.from_("appointments").update({
        "created_at": new_created_at,
        "payment_reminder_sent_at": None,
    }).eq("appointment_id", appt["appointment_id"]).execute()

    deadline_local = deadline_dt.astimezone(TZ)
    deadline_str = deadline_local.strftime("%d/%m/%Y às %H:%M")

    await log_event("payment_deadline_extended", phone, {
        "appointment_id": appt["appointment_id"],
        "new_deadline": deadline_iso,
    })

    return f"Prazo de pagamento estendido até {deadline_str}. O lembrete será reenviado automaticamente."
