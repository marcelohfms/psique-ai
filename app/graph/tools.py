import asyncio
import os
import re
from datetime import datetime, timedelta, date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState

import logging

from app.whatsapp import send_text
from app.database import get_supabase, log_event, upsert_user, get_user_by_phone, get_users_by_phone, DOCTOR_IDS, DOCTOR_NAMES
from app.phone import _phone_variants
from app.chatwoot import get_conversation_id, unassign_agent_bot, add_label
from app.graph.prompts import CORRECT_PIX_KEY

logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/Recife")

async def _notify_clinic(message: str, phone: str = "", subject: str = "Notificação Eva") -> None:
    """Envia notificação para a clínica por e-mail.

    Nunca propaga a exceção — notificação não pode derrubar o fluxo do paciente —
    mas a falha fica auditável no log e no evento `clinic_email_failed`. Engolir
    em silêncio fazia um pagamento registrado parecer notificado (caso Arthur
    Tenório / Camila Brasileiro, 27/07/2026).
    """
    from app.email_sender import send_clinic_notification_email
    try:
        await send_clinic_notification_email(subject, message)
    except Exception as exc:
        logger.exception("CLINIC_EMAIL_FAILED subject=%r phone=%s", subject, phone)
        try:
            await log_event("clinic_email_failed", phone, {
                "subject": subject,
                "reason": str(exc),
                "origin": "bot",
            })
        except Exception:
            logger.exception("CLINIC_EMAIL_FAILED_LOG_EVENT_FAILED subject=%r", subject)


# ── Regex patterns for social name sanitization ──────────────────────────────

_SOCIAL_NAME_AGE_RE = re.compile(r"\s*,?\s*\d+\s*anos?\b", re.IGNORECASE)
_SOCIAL_NAME_PARENS_RE = re.compile(r"\([^)]*\)")


def _sanitize_social_name(raw: str | None) -> str:
    """Remove sufixos comuns que não fazem parte do nome (idade, parênteses)
    antes de salvar o nome social — camada em código além da instrução de
    prompt, que já falhou sozinha na prática para patient_name/user_name."""
    if not raw:
        return ""
    cleaned = _SOCIAL_NAME_PARENS_RE.sub("", raw)
    cleaned = _SOCIAL_NAME_AGE_RE.sub("", cleaned)
    return " ".join(cleaned.split()).strip(" ,.-")


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


def _match_patient_by_name(all_users: list[dict], target: str) -> dict | None:
    """Match a patient record by name among a contact's patients.

    Passes, in order: exact civil name, exact social name, substring civil name.
    Returns None when the target does not single out exactly one patient — the
    caller then falls back to get_user_by_phone, which prefers the ACTIVE patient.

    Desde o #131 esta busca decide também o guard de consulta duplicada, não só o
    insert: um match errado passou a poder bloquear ou liberar o irmão errado. São
    23 contatos com mais de um paciente (famílias que dividem um telefone),
    incluindo o caso Daniela/Silvia Passos (5581981179458, 3 pacientes).
    """
    target = (target or "").strip().lower()
    if not target:
        return None
    for _u in all_users:
        _pname = (_u.get("patient_name") or _u.get("name") or "").strip().lower()
        if _pname == target:
            return _u
    for _u in all_users:
        _sname = (_u.get("social_name") or "").strip().lower()
        if _sname and _sname == target:
            return _u
    # Substring com limite de palavra: sem \b, "Ana" casava com "Mariana Silva" e
    # anexava a consulta ao irmão errado. \b usa \w, que em str Python já inclui
    # letras acentuadas — "Araújo" não quebra o limite.
    _pattern = re.compile(rf"\b{re.escape(target)}\b")
    _hits = [
        _u for _u in all_users
        if _pattern.search((_u.get("patient_name") or _u.get("name") or "").strip().lower())
    ]
    if len(_hits) == 1:
        return _hits[0]
    if len(_hits) > 1:
        # Antes devolvia o primeiro da lista — ou seja, a ordem em que o Supabase
        # respondeu decidia qual irmã era agendada. Sem critério para desempatar,
        # desistir é melhor que chutar: quem chama cai no get_user_by_phone, que
        # ao menos prefere o paciente ATIVO, uma regra definida.
        logger.warning(
            "MATCH_PACIENTE_AMBIGUO: %r casa com %d pacientes do mesmo contato (%s) — "
            "sem desempate, deixando para o get_user_by_phone",
            target, len(_hits), [_u.get("id") for _u in _hits],
        )
    return None


async def _resolve_patient_for_booking(
    phone: str,
    state: dict,
    patient_name_override: str = "",
) -> dict | None:
    """Resolve WHICH patient record a booking refers to, from the contact's phone.

    Single source of truth for confirm_appointment: both the "patient already has an
    appointment" guard and the appointments insert MUST call this, so they can never
    disagree about who is being booked.

    They used to resolve independently — the guard read state["user_db_id"] while the
    insert resolved by phone. state["user_db_id"] is only refreshed by collect_info_node,
    which stops running once stage == "patient_agent", so it freezes at whatever it held
    when registration closed. After the users→patients migration those frozen ids stopped
    matching patients.id (101 of 491 threads em 03/08/2026: 83 apontando para
    patients.legacy_user_id, 18 para ids inexistentes). The guard's .eq("patient_id", <id
    órfão>) then returned nothing, so it never fired, while the insert resolved the right
    patient by phone and created a SECOND active appointment (caso Dione/Pedro Lins De
    Araújo, 5581999578203, 30/07/2026: agendamento de 10/08 ficou órfão, recebeu cobrança
    de taxa e foi auto-cancelado, disparando aviso indevido de "vaga liberada").

    Resolution order (phone first, user_db_id only as a tiebreaker):
      - contact with a single patient (1351 de 1374 contatos): resolved purely by phone,
        so a stale/orphan user_db_id cannot affect it;
      - contact with several patients (23 contatos — famílias que usam um telefone só):
        attendant override → state["user_db_id"] → patient_name. Blocking on ANY of the
        contact's patients would break booking for a sibling (caso Daniela/Silvia Passos,
        5581981179458, onde 3 pacientes dividem o telefone).
    """
    all_users = await get_users_by_phone(phone)
    user = None
    if len(all_users) > 1:
        if patient_name_override.strip():
            # Attendant explicitly named a different patient than the one in
            # conversation context — honor the override.
            user = _match_patient_by_name(all_users, patient_name_override)
        if user is None:
            # Prefer the patient already resolved for this conversation
            # (state["user_db_id"]) over re-deriving from patient_name — patient_name
            # is a plain string that can go stale independently of user_db_id (caso
            # Renata Monteiro / Laila+Suzi Viana, 5581996962165, 08/07/2026: a stale
            # patient_name silently overwrote by app/main.py's DB-sync made this
            # matching attach a new appointment to the wrong twin's patient_id).
            _uid = state.get("user_db_id")
            if _uid:
                user = next((_u for _u in all_users if _u["id"] == _uid), None)
        if user is None:
            _name = state.get("patient_name") or state.get("user_name") or ""
            user = _match_patient_by_name(all_users, _name)
    if user is None:
        # Single-patient contact (or no match above): get_user_by_phone is authoritative.
        user = await get_user_by_phone(phone)
    return user


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


def _times_with_modality(slots: list[tuple[datetime, str]]) -> str:
    """"HH:MM [modalidade], HH:MM [modalidade]" — usado no resumo por turno.
    A etiqueta de modalidade NUNCA pode ser omitida: sem ela a Eva não tem como
    aplicar a regra de MODALIDADE DE ATENDIMENTO do prompt e passa a adivinhar
    (caso 5587996089614, 04/08/2026: um slot "escolha" virou "exclusivamente
    online" na fala da Eva)."""
    return ", ".join(
        f"{slot.strftime('%H:%M')} [{_MOD_LABELS.get(modality, modality)}]"
        for slot, modality in slots
    )


_ANY_DAY_MAX_DAYS_CURRENT_WEEK = 3
_ANY_DAY_MIN_DISTINCT_DAYS = 2
_ANY_DAY_MAX_WEEKS = 8
# "quais dias tem disponíveis em <mês>?" → quantos dias do mês listar de uma vez.
_MONTH_MAX_DAYS_ANY_SHIFT = 5


def _week_range(offset_weeks: int) -> tuple[date, date]:
    """Retorna (início, fim) da janela de busca: offset_weeks=0 é "esta semana"
    (de hoje até domingo); offset_weeks>=1 é uma semana cheia (segunda a
    domingo), offset_weeks semanas após a atual."""
    today = datetime.now(TZ).date()
    if offset_weeks == 0:
        start = today
    else:
        next_monday = today + timedelta(days=7 - today.weekday())
        start = next_monday + timedelta(weeks=offset_weeks - 1)
    end = start + timedelta(days=6 - start.weekday())
    return start, end


def _business_days(start: date, end: date):
    """Percorre cada dia útil (segunda a sexta) de start a end, inclusive."""
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


async def _prefetch_supabase_busy(doctor: str, first_day: date, last_day: date):
    """Busca UMA vez as faixas ocupadas do Supabase para toda a varredura.

    Cada query custa ~260 ms. Sem isso, uma varredura de 8 semanas em 3 turnos faria
    120 queries — ~31 s de espera do paciente para ler sempre os mesmos dados. O
    horizonte inteiro cabe numa consulta só.

    Fail-open: qualquer erro devolve None, e cada dia volta a buscar por conta própria
    (ou segue só com o Calendar). Nunca derruba a busca de horários."""
    try:
        from app.google_calendar import fetch_supabase_busy
        start = datetime(first_day.year, first_day.month, first_day.day, 0, 0, tzinfo=TZ)
        end = datetime(last_day.year, last_day.month, last_day.day, 23, 59, 59, tzinfo=TZ)
        return await fetch_supabase_busy(doctor, start, end)
    except Exception:
        logging.getLogger(__name__).exception(
            "PREFETCH_SUPABASE_BUSY falhou doctor=%s", doctor
        )
        return None


async def _slots_for_any_day(
    day: date, calendar_id: str, doctor: str, preferred_shift: str,
    slot_duration_minutes: int, _get_slots, supabase_busy=None,
) -> dict:
    """Retorna {turno: slots} para o dia informado. Consulta apenas o turno
    pedido, ou os três turnos quando preferred_shift == "qualquer".

    `supabase_busy` é repassado adiante para que a varredura inteira use UMA busca
    ao Supabase — sem isso, os três turnos de cada dia repetiriam a mesma query."""
    if preferred_shift != "qualquer":
        slots = await _get_slots(
            calendar_id=calendar_id,
            preferred_day=day.isoformat(),
            preferred_shift=preferred_shift,
            slot_minutes=slot_duration_minutes,
            doctor_key=doctor,
            supabase_busy=supabase_busy,
        )
        return {preferred_shift: slots} if slots else {}
    result: dict = {}
    for shift_key in ("manha", "tarde", "noite"):
        slots = await _get_slots(
            calendar_id=calendar_id,
            preferred_day=day.isoformat(),
            preferred_shift=shift_key,
            slot_minutes=slot_duration_minutes,
            doctor_key=doctor,
            supabase_busy=supabase_busy,
        )
        if slots:
            result[shift_key] = slots
    return result


def _format_any_day_section(day: date, day_shifts: dict, preferred_shift: str) -> str:
    day_label = _WEEKDAY_LABELS_PT.get(day.weekday(), "")
    date_label = day.strftime("%d/%m")
    header = f"{day_label}, dia {date_label}" if day_label else date_label
    if preferred_shift == "qualquer":
        lines = [f"{header}:"]
        for shift_key, shift_label in [("manha", "manhã"), ("tarde", "tarde"), ("noite", "noite")]:
            slots = day_shifts.get(shift_key)
            if slots:
                lines.append(f"  - {shift_label.capitalize()}: {_times_with_modality(slots)}")
        return "\n".join(lines)
    slots = day_shifts.get(preferred_shift, [])
    lines = [f"{header} ({preferred_shift}):"]
    for i, (slot, modality) in enumerate(slots, 1):
        lines.append(f"  {i}. {slot.strftime('%H:%M')} [{_MOD_LABELS.get(modality, modality)}]")
    return "\n".join(lines)


async def _earliest_slot_dt(calendar_id: str, doctor: str, slot_duration_minutes: int) -> datetime | None:
    """Retorna o datetime do slot disponível mais cedo para o médico, varrendo os
    dias úteis futuros (até _ANY_DAY_MAX_WEEKS semanas). Como os dias são
    percorridos em ordem cronológica, o primeiro dia com vaga contém o slot mais
    cedo. None se nada for encontrado dentro do horizonte."""
    from app.google_calendar import get_available_slots as _get_slots

    _horizon_start, _ = _week_range(0)
    _, _horizon_end = _week_range(_ANY_DAY_MAX_WEEKS)
    _sb_busy = await _prefetch_supabase_busy(doctor, _horizon_start, _horizon_end)

    for offset in range(0, _ANY_DAY_MAX_WEEKS + 1):
        start, end = _week_range(offset)
        for day in _business_days(start, end):
            day_shifts = await _slots_for_any_day(
                day, calendar_id, doctor, "qualquer", slot_duration_minutes, _get_slots,
                supabase_busy=_sb_busy,
            )
            slots = [s[0] for lst in day_shifts.values() for s in lst]
            if slots:
                return min(slots)
    return None


async def _search_month_shift(
    calendar_id: str,
    doctor: str,
    preferred_month_str: str,
    preferred_shift: str,
    slot_duration_minutes: int,
    _get_slots,
) -> str:
    """Busca os primeiros dias de um mês com slots disponíveis.
    preferred_shift == "qualquer" → responde "quais dias tem em <mês>", listando
    os dias com vaga em qualquer turno.
    Se o texto pedir o FINAL do mês ("final de agosto"), busca só a última semana.
    Retorna formatado ou mensagem de indisponibilidade."""
    from app.google_calendar import (
        _month_end_window_start,
        _wants_month_end,
        month_and_year,
    )

    parsed_month = month_and_year(preferred_month_str)
    if parsed_month is None:
        return "Não entendi qual mês. Por favor informe (ex: 'agosto', 'setembro')."
    year, month_num = parsed_month
    today = datetime.now(TZ).date()
    any_shift = preferred_shift == "qualquer"

    # Iterate through all days of that month, collecting available slots.
    # "final de <mês>" = última semana do mês → only the last 7 calendar days.
    from calendar import monthrange
    _, days_in_month = monthrange(year, month_num)
    month_end_only = _wants_month_end(preferred_month_str)
    start_day = _month_end_window_start(year, month_num).day if month_end_only else 1

    # Sem turno definido a pergunta é "quais DIAS tem no mês" → vale a pena
    # mostrar mais dias (e menos horários por dia) do que numa busca por turno.
    max_days = _MONTH_MAX_DAYS_ANY_SHIFT if any_shift else 3
    max_slots_per_day = 3 if any_shift else 2

    _sb_busy = await _prefetch_supabase_busy(
        doctor, date(year, month_num, start_day), date(year, month_num, days_in_month)
    )

    slots_by_day = {}
    for day_num in range(start_day, days_in_month + 1):
        try_date = date(year, month_num, day_num)
        # Skip weekends and past dates
        if try_date.weekday() >= 5 or try_date < today:
            continue

        slots = await _get_slots(
            calendar_id=calendar_id,
            preferred_day=try_date.isoformat(),
            preferred_shift=preferred_shift,
            slot_minutes=slot_duration_minutes,
            doctor_key=doctor,
            supabase_busy=_sb_busy,
        )

        if slots:
            slots_by_day[try_date] = slots
            if len(slots_by_day) >= max_days:
                break

    month_name_pt = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                     "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"][month_num]
    shift_suffix = "" if any_shift else f" para o turno da {preferred_shift}"

    if not slots_by_day:
        if month_end_only:
            return (
                f"Não há horários disponíveis no final de {month_name_pt} "
                f"(última semana do mês){shift_suffix}. "
                "NÃO ofereça dias do início ou do meio do mês — o paciente pediu o final. "
                "Pergunte se prefere outro turno, o início do mês seguinte ou outro período."
            )
        return (
            f"Não há horários disponíveis em {month_name_pt}{shift_suffix}. "
            "Deseja tentar outro turno ou outro mês?"
        )

    # Format the response
    if month_end_only:
        header_line = f"Horários disponíveis no final de {month_name_pt} (última semana){shift_suffix}:"
    elif any_shift:
        header_line = f"Dias com horários disponíveis em {month_name_pt}:"
    else:
        header_line = f"Primeiros horários disponíveis em {preferred_shift}:"
    lines = [header_line]
    for day, day_slots in sorted(slots_by_day.items())[:max_days]:
        day_label = _WEEKDAY_LABELS_PT.get(day.weekday(), "")
        date_label = day.strftime("%d/%m")
        header = f"{day_label}, dia {date_label}" if day_label else date_label
        if any_shift:
            times = _times_with_modality(day_slots[:max_slots_per_day])
            more = " (e outros)" if len(day_slots) > max_slots_per_day else ""
            lines.append(f"- {header}: {times}{more}")
        else:
            for slot, modality in day_slots[:max_slots_per_day]:
                lines.append(f"  {slot.strftime('%H:%M')} em {header} [{_MOD_LABELS.get(modality, modality)}]")

    if any_shift:
        lines.append(
            "\nEstes são os primeiros dias do mês com vaga (pode haver mais adiante). "
            "Pergunte qual dia o paciente prefere."
        )
    else:
        lines.append("\nQual horário o paciente prefere?")
    return "\n".join(lines)


async def pick_doctor_by_earliest_availability(
    candidates: list[str], slot_duration_minutes: int = 60,
) -> str | None:
    """Entre os médicos candidatos (já filtrados por idade), retorna o que tem o
    slot disponível mais cedo — a "agenda mais próxima". A Dra. Bruna sempre usa
    slots de 60min (mesmo em 1ª consulta de menor). Empate ou falha de agenda →
    primeiro candidato (fallback determinístico, nunca deixa sem médico)."""
    best_doctor: str | None = None
    best_dt: datetime | None = None
    for doctor in candidates:
        calendar_id = await _get_doctor_calendar_id(doctor)
        if not calendar_id:
            continue
        dur = 60 if doctor == "bruna" else slot_duration_minutes
        dt = await _earliest_slot_dt(calendar_id, doctor, dur)
        if dt is None:
            continue
        if best_dt is None or dt < best_dt:
            best_dt, best_doctor = dt, doctor
    return best_doctor or (candidates[0] if candidates else None)


async def _search_any_day(calendar_id: str, doctor: str, preferred_shift: str, slot_duration_minutes: int) -> str:
    """Busca dias úteis futuros (qualquer dia da semana) quando o paciente não
    tem preferência de dia (ex: "qualquer dia"). Busca primeiro a semana atual
    (até 3 dias distintos com vaga); se encontrar menos de 2 dias distintos,
    também busca a semana seguinte inteira; se ainda assim não achar nada,
    continua expandindo semana a semana (limite de segurança) até achar algo —
    nunca informa ao paciente que "não encontrou"."""
    from app.google_calendar import get_available_slots as _get_slots

    _horizon_start, _ = _week_range(0)
    _, _horizon_end = _week_range(_ANY_DAY_MAX_WEEKS)
    _sb_busy = await _prefetch_supabase_busy(doctor, _horizon_start, _horizon_end)

    found: list[tuple[date, dict]] = []

    start, end = _week_range(0)
    for day in _business_days(start, end):
        day_shifts = await _slots_for_any_day(
            day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots,
            supabase_busy=_sb_busy,
        )
        if day_shifts:
            found.append((day, day_shifts))
            if len(found) >= _ANY_DAY_MAX_DAYS_CURRENT_WEEK:
                break

    extended = False
    if len(found) < _ANY_DAY_MIN_DISTINCT_DAYS:
        extended = True
        start, end = _week_range(1)
        for day in _business_days(start, end):
            day_shifts = await _slots_for_any_day(
                day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots,
                supabase_busy=_sb_busy,
            )
            if day_shifts:
                found.append((day, day_shifts))

    week_offset = 2
    while len(found) < _ANY_DAY_MIN_DISTINCT_DAYS and week_offset <= _ANY_DAY_MAX_WEEKS:
        extended = True
        start, end = _week_range(week_offset)
        for day in _business_days(start, end):
            day_shifts = await _slots_for_any_day(
                day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots,
                supabase_busy=_sb_busy,
            )
            if day_shifts:
                found.append((day, day_shifts))
        week_offset += 1

    if not found:
        return (
            "Não encontrei horários disponíveis nas próximas semanas. "
            "Use transfer_to_human para encaminhar ao atendente humano verificar outras opções."
        )

    sections = [_format_any_day_section(day, day_shifts, preferred_shift) for day, day_shifts in found]
    prefix = (
        "Poucos horários disponíveis na semana atual — incluí também outras semanas:\n\n"
        if extended else ""
    )
    return prefix + "\n\n".join(sections)


async def _search_week(
    week_offset: int, calendar_id: str, doctor: str,
    preferred_shift: str, slot_duration_minutes: int,
) -> str:
    """Lista os horários de UMA semana específica (offset em relação à atual):
    week_offset=0 → dias úteis restantes desta semana; week_offset>=1 → seg–sex
    daquela semana. Diferente de _search_any_day, não há teto de dias — a semana
    já é um intervalo limitado. Se a semana alvo não tiver nenhuma vaga, delega a
    _search_any_day para oferecer os próximos dias com vaga."""
    from app.google_calendar import get_available_slots as _get_slots

    start, end = _week_range(week_offset)
    _sb_busy = await _prefetch_supabase_busy(doctor, start, end)

    found: list[tuple[date, dict]] = []
    for day in _business_days(start, end):
        day_shifts = await _slots_for_any_day(
            day, calendar_id, doctor, preferred_shift, slot_duration_minutes, _get_slots,
            supabase_busy=_sb_busy,
        )
        if day_shifts:
            found.append((day, day_shifts))

    if not found:
        return await _search_any_day(
            calendar_id=calendar_id,
            doctor=doctor,
            preferred_shift=preferred_shift,
            slot_duration_minutes=slot_duration_minutes,
        )

    sections = [_format_any_day_section(day, day_shifts, preferred_shift) for day, day_shifts in found]
    return "\n\n".join(sections)


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
    Se preferred_day for um dia da semana (ex: "quarta"), busca até 4 semanas à frente.

    FORMATO DE DATAS:
    - Data específica: sempre dd/mm (ex: "17/06"), NUNCA nome do dia da semana.
    - Com mês: inclua o mês (ex: "quinta de agosto").
    - "quais dias tem em <mês>?" / "disponibilidade de setembro": passe só o nome do
      mês (ex: "setembro") e preferred_shift="qualquer" — a ferramenta varre o mês e
      devolve os DIAS com vaga. Não converta o mês em uma data (isso responderia
      sobre um dia que o paciente não pediu).
    - "final de <mês>": passe a expressão completa (ex: "final de agosto"), não só o mês.
    - Dia + número juntos (ex: "sexta, dia 14"): use dd/mm (ex: "14/08").
    - "próxima semana": inclua com o dia (ex: "quarta-feira da próxima semana").

    NUNCA diga que uma data não tem horário sem antes chamar esta ferramenta com
    exatamente essa data em dd/mm e receber "não há horários" como resultado — não
    infira indisponibilidade a partir de uma busca por dia da semana que retornou
    outra data.

    slot_duration_minutes: 120 para primeira consulta <18 anos, 60 para outros casos.
    preferred_shift: use "qualquer" para verificar todos os turnos e oferecer opções.
    """
    result = await _get_available_slots_impl(
        preferred_day, preferred_shift, slot_duration_minutes, state, config
    )
    # Rastreio de "pediu data e não continuou" (carrinho abandonado de consulta):
    # registra a oferta SÓ quando horários reais foram apresentados. O texto de uma
    # oferta sempre traz ao menos um HH:MM, enquanto mensagens de indisponibilidade,
    # erro ou restrição cadastral não — então servem de discriminador barato e
    # robusto a reformulações. Fire-and-forget: nunca deixa o rastreio quebrar o fluxo.
    try:
        if re.search(r"\b\d{1,2}:\d{2}\b", result):
            await log_event(
                "slots_offered",
                config["configurable"]["phone"],
                {
                    "doctor": await _resolve_doctor(state, config),
                    "preferred_day": preferred_day,
                    "preferred_shift": preferred_shift,
                    "slot_duration_minutes": slot_duration_minutes,
                },
            )
    except Exception:
        logger.exception("Falha ao registrar slots_offered (ignorada)")
    return result


async def _get_available_slots_impl(
    preferred_day: str,
    preferred_shift: Literal["manha", "tarde", "noite", "qualquer"],
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Implementação de get_available_slots (sem o decorator @tool). Toda a lógica
    de busca e formatação de horários vive aqui; o wrapper público acima registra
    o evento slots_offered quando esta função devolve horários reais. O contrato
    voltado ao LLM está no docstring do wrapper."""
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

    now = datetime.now(TZ)
    shift_norm = preferred_shift.lower().replace("ã", "a").replace("manhã", "manha").strip()
    shift_start_h, shift_end_h = SHIFT_HOURS.get(shift_norm, (8, 18))

    # ── Detect weekday name so the branches below only run for real day values ─
    preferred_day_norm = preferred_day.lower().strip()
    weekday_key = next(
        (wd for name, wd in _WEEKDAYS_PT.items() if name in preferred_day_norm),
        None,
    )

    # ── Mês inteiro, sem dia ("setembro", "final de agosto", "quais dias tem em
    # outubro") → varre o mês. Precisa vir ANTES dos ramos que chamam _parse_day:
    # um mês não é uma data, e _parse_day devolve None para ele de propósito ─────
    from app.google_calendar import is_month_only
    if weekday_key is None and is_month_only(preferred_day_norm):
        return await _search_month_shift(
            calendar_id=calendar_id,
            doctor=doctor,
            preferred_month_str=preferred_day,
            preferred_shift=preferred_shift,
            slot_duration_minutes=slot_duration_minutes,
            _get_slots=_get_slots,
        )

    # ── No day preference (e.g. "qualquer dia", "tanto faz") → search upcoming
    # business days regardless of weekday, expanding to later weeks if needed ──
    _no_day_pref_patterns = ("qualquer", "tanto faz")
    if weekday_key is None and any(p in preferred_day_norm for p in _no_day_pref_patterns):
        return await _search_any_day(
            calendar_id=calendar_id,
            doctor=doctor,
            preferred_shift=preferred_shift,
            slot_duration_minutes=slot_duration_minutes,
        )

    # ── Expressão de semana (sem dia específico) → oferece a relação da semana
    # em vez de perguntar o dia. Precisa vir ANTES do branch preferred_shift ==
    # "qualquer": para "próxima semana" + shift "qualquer", _parse_day devolve
    # None e aquele branch responderia "Não entendi a data". ──────────────────
    if weekday_key is None:
        _next_week_markers = ("xima semana", "semana que vem", "semana seguinte")
        _this_week_markers = ("essa semana", "esta semana", "dessa semana", "desta semana")
        if any(m in preferred_day_norm for m in _next_week_markers):
            return await _search_week(
                week_offset=1, calendar_id=calendar_id, doctor=doctor,
                preferred_shift=preferred_shift, slot_duration_minutes=slot_duration_minutes,
            )
        if any(m in preferred_day_norm for m in _this_week_markers):
            return await _search_week(
                week_offset=0, calendar_id=calendar_id, doctor=doctor,
                preferred_shift=preferred_shift, slot_duration_minutes=slot_duration_minutes,
            )
        # Catch-all deliberadamente amplo: qualquer menção a "semana" sem dia nomeado
        # (incl. "fim de semana") cai em "próximos dias com vaga" em vez de travar.
        if "em breve" in preferred_day_norm or "semana" in preferred_day_norm:
            return await _search_any_day(
                calendar_id=calendar_id, doctor=doctor,
                preferred_shift=preferred_shift, slot_duration_minutes=slot_duration_minutes,
            )

    # ── "qualquer" shift: check all shifts and return summary ─────────────────
    if preferred_shift == "qualquer":
        base_date_q = _parse_day(preferred_day)
        if base_date_q is None:
            return "Não entendi a data. Por favor informe um dia específico (ex: segunda, 19/05, amanhã)."

        # For weekday names: try up to 4 weeks until we find a date with slots.
        # For specific dates: single attempt only.
        max_weeks = 4 if weekday_key is not None else 1
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
                    sections.append(f"- {shift_label.capitalize()}: {_times_with_modality(slots)}")
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
                        single_sections.append(f"- {shift_label.capitalize()}: {_times_with_modality(slots_1h)}")
                if single_sections:
                    return (
                        f"Há horários disponíveis em {header}, mas não em bloco de 2 horas seguidas:\n"
                        + "\n".join(single_sections)
                        + "\nInforme o paciente que não há 2 horas consecutivas disponíveis neste dia. "
                        "Pergunte se prefere verificar outro dia com 2 horas consecutivas disponíveis."
                    )
            # No slots at all this week — try the next occurrence (only for weekday names)
        return f"Não há horários disponíveis para {header}. Deseja tentar outro dia?"

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
    if target_date is None:
        # Data não reconhecida (ex: "esse mês", "sei lá"). Não invente um dia —
        # responder sobre uma data que o paciente não pediu é pior que perguntar.
        return (
            f"CLARIFICAÇÃO NECESSÁRIA: não consegui interpretar '{preferred_day}' como uma data. "
            "Se o paciente falou de um mês inteiro, chame get_available_slots de novo com o nome do mês "
            "(ex: 'setembro') e preferred_shift='qualquer'. Caso contrário, pergunte o dia desejado "
            "(ex: 'quarta', '19/05', 'amanhã')."
        )
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
                # Don't rely on the model to remember to call transfer_to_human on a
                # follow-up turn — it may just tell the patient it will transfer and
                # never actually invoke the tool, leaving the bot active and nobody
                # notified. Trigger the real handoff right here instead.
                handoff_message = await transfer_to_human.coroutine(
                    reason=(
                        "Paciente pediu agendamento hoje dentro das próximas 4 horas — "
                        "apenas a atendente pode verificar encaixes com tão pouca antecedência."
                    ),
                    state=state,
                    config=config,
                )
                return (
                    "Não é possível agendar com menos de 4 horas de antecedência. "
                    + handoff_message
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
    slot_datetime: ISO 8601 em horário local de Recife (ex: '2026-03-19T08:00:00').
      Não converta para UTC — feito internamente.
    session_note: para minores, identifique sessões separadas (ex: '1ª hora — responsáveis');
      deixe vazio para consultas normais.
    modality: "online" ou "presencial". Para "presencial requer confirmação", use
      transfer_to_human antes de chamar confirm_appointment.
    force_encaixe: apenas com solicitação explícita da atendente. NÃO usar se o paciente
      já tem consulta futura — use mark_reschedule_in_progress + reschedule_appointment.
    patient_name_override: para agendamentos com múltiplos pacientes no contato.
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

    # Encaixe da Dra. Bruna começando a :20 termina no topo da hora (40min) para
    # não bloquear o slot regular da hora seguinte no busy-check (ex: sexta 13:20 →
    # 14:00, mantém o slot das 14h agendável). Só vale para encaixe (force_encaixe).
    if force_encaixe and doctor == "bruna" and start.minute == 20:
        slot_duration_minutes = 40

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

        # Dr. Júlio: além do início, exigir que o slot INTEIRO (início + duração)
        # caiba numa única janela — senão um bloco de 2h começando no fim da grade
        # (ex: 19:00 numa quinta que fecha 20:00) seria gravado estourando o
        # expediente (caso Bernardo/Mônica 5581991320003, 09/07/2026: 1ª consulta
        # gravada 19:00–21:00). get_available_slots já respeita isso; o confirm não.
        if doctor == "julio":
            from app.google_calendar import merge_adjacent_windows
            _merged_wins = merge_adjacent_windows(_day_wins)
            _slot_end_min = _slot_min + slot_duration_minutes
            if not any(
                (sh * 60 + sm) <= _slot_min and _slot_end_min <= (eh * 60 + em)
                for sh, sm, eh, em, _ in _merged_wins
            ):
                _dur_h = slot_duration_minutes // 60
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"Não há bloco de {_dur_h}h seguidas a partir das {start.strftime('%H:%M')} na grade de "
                    f"{_doctor_label} no dia {start.strftime('%d/%m/%Y')} — o horário ultrapassaria o fim do expediente. "
                    "Chame get_available_slots novamente para um horário que comporte a duração, ou ofereça "
                    "agendar os dois momentos da 1ª consulta em sessões separadas de 1h."
                )

    # Guard 0: block if patient already has a future scheduled OR pending_reschedule
    # appointment (different slot). Forces Eva to use mark_reschedule_in_progress →
    # reschedule_appointment instead. pending_reschedule must be included here — otherwise
    # a mid-reschedule appointment (already marked pending_reschedule by
    # mark_reschedule_in_progress) becomes invisible to this guard, and confirm_appointment
    # can slip through and INSERT a brand-new appointment row instead of updating the
    # existing one, losing the already-paid booking fee (caso Tiago Perrelli, 03/07/2026).
    # Runs even when force_encaixe=True — encaixe only bypasses schedule-window/conflict
    # checks below, never the "patient already has an appointment" check (caso Gustavo
    # Lapenda, 06/07/2026: atendente pediu para "encaixar" um novo horário em vez de
    # remarcar, e o encaixe pulou esse guard, criando dois agendamentos ativos).
    #
    # A 1ª sessão da consulta dividida, quando o guard libera a 2ª (ver exceção abaixo).
    # Fica fora do try para o insert conseguir ler a taxa já paga mesmo se o guard falhar.
    # ── Rede de segurança multi-paciente ──────────────────────────────────────
    # Para contatos que administram vários pacientes (irmãos no mesmo telefone), só
    # prosseguir se patient_name_override singularizar UM paciente. Sem override único,
    # _resolve_patient_for_booking cairia no user_db_id/patient_name congelados e gravaria
    # o irmão errado (caso Renata/Laila+Suzi, 5581996962165, 14/08/2026: consulta pedida
    # para Laila nasceu sob Suzi). _match_patient_by_name devolve None para override vazio,
    # typo ou nome que casa com >1 irmão — em todos esses casos pedimos o nome completo em
    # vez de agendar no escuro. Fica ANTES do create_event/insert (nunca cria evento sob o
    # irmão errado) e usa as MESMAS funções de _resolve_patient_for_booking (paridade).
    _phone_sn = config["configurable"]["phone"].replace("@s.whatsapp.net", "")
    try:
        _all_users_sn = await get_users_by_phone(_phone_sn)
    except Exception:
        # Supabase indisponível: não dá para avaliar multi-paciente aqui. Segue o fluxo
        # normal, que trata a falha de resolução adiante com rollback do evento do Calendar.
        # Loga (como o guard abaixo) para deixar rastro se a degradação mascarar algo.
        _logger.warning(
            "confirm_appointment: rede multi-paciente não avaliada (get_users_by_phone falhou) phone=%s",
            _phone_sn, exc_info=True,
        )
        _all_users_sn = []
    if len(_all_users_sn) > 1 and _match_patient_by_name(_all_users_sn, patient_name_override) is None:
        _names_sn = ", ".join(
            u.get("patient_name") or u.get("name") or "Paciente" for u in _all_users_sn
        )
        _logger.warning(
            "confirm_appointment: contato multi-paciente sem override único — pedindo nome. phone=%s",
            _phone_sn,
        )
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Este contato administra mais de um "
            f"paciente ({_names_sn}) e não deu para identificar com segurança para qual a "
            "consulta é. Pergunte ao contato: 'Qual o nome completo do paciente para quem "
            "deseja agendar?' e rechame confirm_appointment com esse nome em "
            "patient_name_override."
        )

    _split_sibling: dict | None = None
    try:
        _supabase = await get_supabase()
        _phone = config["configurable"]["phone"]
        _phone_clean = _phone.replace("@s.whatsapp.net", "")
        from datetime import timezone as _tz
        _now_iso = datetime.now(_tz.utc).isoformat()

        # Resolve patient_ids via contacts → patient_contacts, then filter to CURRENT patient.
        # Guard should block only if THIS patient already has a future appointment, not if
        # another patient in the contact does (caso Daniela/Silvia: Silvia has appointment
        # on 20/07, but Daniela is free on 26/08 — must allow booking for Daniela).
        #
        # Resolves through the SAME helper the insert below uses. Reading
        # state["user_db_id"] directly here made the guard blind whenever that id was
        # stale, while the insert still resolved the patient correctly by phone — see
        # _resolve_patient_for_booking for the full failure mode.
        _patient = await _resolve_patient_for_booking(_phone_clean, state, patient_name_override)
        _patient_id = (_patient or {}).get("id")
        if _patient_id:
            _appts_r = await _supabase.from_("appointments").select(
                "appointment_id, start_time, status, consultation_type, "
                "booking_fee_paid_at, booking_fee_waived"
            ).eq("patient_id", _patient_id).in_("status", ["scheduled", "pending_reschedule"]).execute()
            # Filtro por data feito aqui (não com .gte na query) para diferenciar por status:
            #   - pending_reschedule bloqueia SEMPRE, independente da data. É o sinal durável
            #     "remarcação pendente, taxa já paga", e seu start_time é o do slot ANTIGO —
            #     que fica no passado quanto mais o paciente demora a voltar. Um .gte("start_time",
            #     now) tornava essa linha invisível e deixava confirm_appointment criar um
            #     agendamento novo com nova taxa (caso Heitor/Ludmilla, 5581996937559,
            #     pending_reschedule de 02/07 remarcado semanas depois).
            #   - scheduled só bloqueia se for FUTURO. Um scheduled no passado é consulta já
            #     atendida (ainda não marcada completed) e não deve travar um novo agendamento.
            _other_appts = []
            for _a in (_appts_r.data or []):
                if _a["start_time"] == start.isoformat():
                    continue  # mesmo slot sendo reconfirmado — não é conflito
                if _a.get("status") == "pending_reschedule":
                    _other_appts.append(_a)
                elif _a.get("status") == "scheduled" and _a["start_time"] >= _now_iso:
                    _other_appts.append(_a)
            # EXCEÇÃO — 2ª sessão da 1ª consulta de menor dividida em duas partes de 1h.
            # A Eva oferece explicitamente dividir a primeira consulta do menor com o
            # Dr. Júlio (1h com os responsáveis + 1h com o paciente), inclusive em dias
            # diferentes. Sem essa exceção o Guard 0 lê a 1ª sessão como "paciente já tem
            # consulta futura" e manda remarcar: a Eva chama reschedule_appointment, o
            # evento da 1ª sessão é apagado do Calendar e a MESMA linha é movida para a
            # data da 2ª — o paciente fica com metade da consulta que agendou (caso
            # Marcelo Rodrigues de Souza Brayner Filho, 5581999865181, 04/08/2026: a
            # sessão de 06/08 09:00 com os responsáveis sumiu da agenda do Dr. Júlio).
            #
            # Estreita de propósito, para não reabrir os buracos que o guard fecha:
            #   - session_note preenchido (é o único sinal explícito de "isto é uma das
            #     partes", e é o mesmo que já governa consultation_type no insert);
            #   - menor, primeira consulta, Dr. Júlio, sessão de 1h;
            #   - exatamente UMA consulta existente conflitando (a 1ª parte) — a consulta
            #     dividida tem duas partes, uma terceira volta a ser bloqueada;
            #   - essa consulta é `scheduled` e `primeira_consulta`. pending_reschedule
            #     nunca entra na exceção: é remarcação de verdade em curso, com taxa presa
            #     à linha, e criar uma linha nova perderia a taxa (caso Tiago Perrelli).
            if (
                session_note
                and _other_appts
                and len(_other_appts) == 1
                and slot_duration_minutes == 60
                and (state.get("patient_age") or 99) < 18
                and state.get("preferred_doctor") == "julio"
                and _other_appts[0].get("status") == "scheduled"
                and _other_appts[0].get("consultation_type") == "primeira_consulta"
            ):
                _split_sibling = _other_appts[0]
                _other_appts = []
                _logger.info(
                    "confirm_appointment: 2ª sessão da 1ª consulta dividida liberada — "
                    "patient=%s sibling=%s session_note=%s",
                    _patient_id, _split_sibling.get("appointment_id"), session_note,
                )

            if _other_appts:
                    from zoneinfo import ZoneInfo as _ZI
                    _TZ = _ZI("America/Recife")
                    _existing_dates = ", ".join(
                        datetime.fromisoformat(a["start_time"]).astimezone(_TZ).strftime("%d/%m/%Y às %H:%M")
                        + f" (ID: {a['appointment_id']})"
                        for a in _other_appts
                    )
                    _logger.warning("confirm_appointment: patient already has scheduled appt(s) — blocking phone=%s", _phone_clean)
                    # Menor em 1ª consulta dividida que chegou aqui SEM session_note: a
                    # instrução genérica abaixo ("remarque") é exatamente o caminho errado
                    # (caso Marcelo Filho). Diz antes como agendar a 2ª sessão direito.
                    _split_hint = ""
                    if (
                        not session_note
                        and slot_duration_minutes == 60
                        and (state.get("patient_age") or 99) < 18
                        and state.get("preferred_doctor") == "julio"
                        and any(a.get("consultation_type") == "primeira_consulta"
                                and a.get("status") == "scheduled" for a in _other_appts)
                    ):
                        _split_hint = (
                            "ATENÇÃO: se este agendamento é a 2ª parte da primeira consulta "
                            "dividida (1h com os responsáveis + 1h com o paciente), NÃO remarque — "
                            "chame confirm_appointment de novo com "
                            'session_note="2ª hora — paciente". '
                        )
                    # Encaixe (force_encaixe): a atendente já definiu o horário exato,
                    # inclusive fora da grade padrão. NÃO mande passar por
                    # get_available_slots — ele só conhece a grade e derruba o horário do
                    # encaixe, fazendo a Eva dizer "não há horários" e travar (caso Leticia
                    # Pimentel, 5581996332827, 24/08/2026). Manda remarcar DIRETO para o
                    # slot_datetime do encaixe, preservando force_encaixe no reschedule.
                    if force_encaixe:
                        _one_id = (
                            _other_appts[0]["appointment_id"] if len(_other_appts) == 1 else None
                        )
                        _id_hint = (
                            f"appointment_id='{_one_id}'" if _one_id
                            else "o appointment_id da consulta existente"
                        )
                        _mod_hint = (
                            f", modality='{modality}'" if modality in ("online", "presencial") else ""
                        )
                        return (
                            f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                            f"O paciente já tem consulta(s) agendada(s): {_existing_dates}. "
                            f"{_split_hint}"
                            "NÃO crie um novo agendamento — o encaixe pedido pela atendente "
                            "significa REMARCAR a consulta existente para o novo horário, não criar "
                            "uma segunda. Este horário é um ENCAIXE fora da grade: NÃO chame "
                            "get_available_slots (ele não lista horários de encaixe). OBRIGATÓRIO: "
                            f"chame mark_reschedule_in_progress com {_id_hint} e, em seguida, "
                            f"reschedule_appointment com new_slot_datetime='{slot_datetime}'"
                            f"{_mod_hint}, force_encaixe=True. Nunca retorne erro ao paciente por "
                            "causa disso."
                        )
                    return (
                        f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                        f"O paciente já tem consulta(s) agendada(s): {_existing_dates}. "
                        f"{_split_hint}"
                        "NÃO crie um novo agendamento, mesmo que a atendente tenha pedido para "
                        "'encaixar' um novo horário — isso significa remarcar a consulta existente, "
                        "não criar uma segunda. OBRIGATÓRIO: chame imediatamente "
                        "mark_reschedule_in_progress com o appointment_id da consulta existente, "
                        "depois get_available_slots, depois reschedule_appointment. "
                        "Nunca retorne erro ao paciente por causa disso."
                    )
    except Exception:
        # Non-fatal — proceed, but never silently. A swallowed error here means the
        # "patient already has an appointment" guard did not run at all, and the only
        # symptom is a duplicate appointment discovered days later. Deliberately NOT
        # fail-closed: a transient Supabase error must not block a legitimate booking.
        _logger.exception(
            "confirm_appointment: guard de agendamento existente FALHOU (seguindo sem bloquear) phone=%s",
            config["configurable"]["phone"],
        )

    # Double-check slot is still free before booking — skipped for encaixe
    if not force_encaixe:
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

        # Guard 1b: same slot already held by ANOTHER patient of this doctor, per Supabase.
        # Guard 1 only catches the same patient; Guard 2 checks the Calendar, which is blind
        # to a `scheduled` row whose event is missing — exactly the fantasma slot that made
        # the clinic sell one 17h to two patients (caso Maria Clara). fetch_supabase_busy now
        # stops get_available_slots from OFFERING such a slot; this is the confirm-time
        # backstop for the one that slips through anyway (e.g. a stale link reused). Overlap
        # semantics (start < slot_end AND end > slot_start) mirror fetch_supabase_busy so a
        # longer appointment straddling the slot is caught too.
        try:
            _supabase = await get_supabase()
            _phone = config["configurable"]["phone"]
            _self_pids = {u["id"] for u in await get_users_by_phone(_phone)}
            _doctor_id = DOCTOR_IDS.get(doctor)
            _slot_end_check = start + timedelta(minutes=slot_duration_minutes)
            if _doctor_id:
                _clash = await _supabase.from_("appointments").select(
                    "appointment_id, patient_id"
                ).eq("doctor_id", _doctor_id).eq("status", "scheduled").lt(
                    "start_time", _slot_end_check.isoformat()
                ).gt("end_time", start.isoformat()).execute()
                _others = [r for r in (_clash.data or []) if r.get("patient_id") not in _self_pids]
                if _others:
                    _logger.warning(
                        "confirm_appointment: cross-patient slot clash doctor=%s slot=%s conflicting=%s",
                        doctor, start.isoformat(), [r.get("appointment_id") for r in _others],
                    )
                    return (
                        f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                        f"Este horário ({start.strftime('%d/%m/%Y às %H:%M')}) já está ocupado por outra consulta. "
                        "Avise o paciente com empatia que o horário foi preenchido e chame get_available_slots novamente para buscar outro horário disponível."
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

    # Always use the canonical `patients.name` from the DB for the Calendar
    # event and clinic notification below — patient_name_override/state can
    # carry the attendant's raw wording (e.g. an ALL CAPS name copied from a
    # private note), and both must follow the standard format regardless of
    # how it arrived (caso João Pedro Lins Da Costa Gomes / Ednara de Morais
    # Lins, 5581992349207, 2026-07-27: nota da atendente em CAIXA ALTA foi
    # parar sem normalização no evento do Calendar e no e-mail da clínica).
    social_name = None
    try:
        _name_candidates = await get_users_by_phone(config["configurable"]["phone"])
        _canonical_user = None
        if len(_name_candidates) > 1:
            _target = patient_name.strip().lower()
            _canonical_user = next(
                (c for c in _name_candidates if (c.get("patient_name") or "").strip().lower() == _target), None
            ) or next(
                (c for c in _name_candidates if (c.get("social_name") or "").strip().lower() == _target), None
            ) or next(
                (c for c in _name_candidates if _target in (c.get("patient_name") or "").strip().lower()), None
            )
        elif _name_candidates:
            _canonical_user = _name_candidates[0]
        if _canonical_user and _canonical_user.get("patient_name"):
            patient_name = _canonical_user["patient_name"]
        if _canonical_user:
            social_name = _canonical_user.get("social_name")
    except Exception:
        _logger.exception("CONFIRM_DEBUG canonical name lookup failed, using raw patient_name=%s", patient_name)

    # Nome Civil (Nome Social): nome civil primeiro (casa com CPF/prontuário,
    # fica auditável), nome social entre parênteses avisa o médico como chamar
    # o paciente. Só para uso interno (Calendar, e-mail da clínica) — a Eva usa
    # só o nome social ao se dirigir ao paciente (ver app/graph/nodes.py).
    calendar_display_name = f"{patient_name} ({social_name})" if social_name else patient_name

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
            patient_name=calendar_display_name,
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

    phone = config["configurable"]["phone"]

    # Everything below persists the booking (Supabase). ANY failure in this block —
    # not just the final insert — must roll back the Calendar event created above.
    # Previously only the insert() call was guarded, so an exception raised while
    # resolving the patient (get_users_by_phone/_match_by_name, both hit Supabase)
    # left the Calendar event orphaned with no appointments row and no guard against
    # recreation, so retries kept creating duplicate events indefinitely (caso Silvia
    # De Souza Passos, 5581998483157, 5 eventos órfãos criados em ~3min30s no
    # calendário do Dr. Júlio em 10/07/2026, dia 24/07 11h, sem nenhuma linha
    # correspondente em appointments/events/messages).
    try:
        _weekday_name = _WEEKDAY_LABELS_PT.get(start.weekday(), "")
        formatted = f"{_weekday_name}, {start.strftime('%d/%m/%Y às %H:%M')}" if _weekday_name else start.strftime("%d/%m/%Y às %H:%M")

        end = start + timedelta(minutes=slot_duration_minutes)
        client = await get_supabase()

        # When the contact has multiple patients, resolve the correct patient record.
        # get_user_by_phone returns an arbitrary record — wrong when contact has e.g. parent + child.
        # Same helper the guard above uses: the two MUST agree on who is being booked.
        user = await _resolve_patient_for_booking(phone, state, patient_name_override)

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

        # 2ª sessão da 1ª consulta dividida: a taxa de reserva é UMA só para a primeira
        # consulta inteira e já foi paga na 1ª sessão. Sem herdar o timestamp, a linha da
        # 2ª sessão nasce com booking_fee_paid_at nulo, send_payment_reminders cobra a
        # taxa de novo e acaba auto-cancelando um horário que está pago.
        if _split_sibling:
            _bfw = _bfw or bool(_split_sibling.get("booking_fee_waived"))
            _bfp_at = _split_sibling.get("booking_fee_paid_at") or _bfp_at

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
    except Exception as e:
        _logger.error("CONFIRM_DEBUG persist FAILED (rolling back calendar event): %s", e, exc_info=True)
        from app.google_calendar import cancel_event
        try:
            await cancel_event(calendar_id, event_id)
        except Exception:
            _logger.exception("CONFIRM_DEBUG cancel_event rollback ALSO FAILED event_id=%s", event_id)
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
        f"Paciente: {calendar_display_name}{session_label}\n"
        f"Data e horário: {formatted}\n"
        f"Médico(a): {doctor_label}"
        f"{modality_line}\n\n"
        f"📋 LEMBRETE: enviar o Termo de Compromisso para o e-mail do paciente ({patient_email})."
        f"{registration_block}",
        phone=phone,
        subject=f"Agendamento realizado — {calendar_display_name}",
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
    appt_result = await client.from_("appointments").select("start_time, booking_fee_paid_at, patient_id").eq("appointment_id", appointment_id).maybe_single().execute()
    old_start_time = (appt_result.data or {}).get("start_time")
    fee_was_paid = bool((appt_result.data or {}).get("booking_fee_paid_at"))
    _this_patient_id = (appt_result.data or {}).get("patient_id")

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

    # ── Aviso de consultas irmãs ainda ativas ────────────────────────────────
    # A primeira consulta infantil é dividida em DOIS agendamentos que coexistem
    # (responsáveis + paciente), ambos com o MESMO patient_id. Quando o pedido é
    # "cancelar as consultas", a Eva precisa cancelar os dois — mas o LLM pode
    # anunciar cancelar todos e emitir só uma chamada, reportando ambos como
    # canceladas (caso Marcelo Brayner, 5581999865181, 13/08/2026: cancelou a de
    # 17/08 mas deixou a de 24/08 scheduled). Aqui a própria tool que rodou avisa,
    # via instrução interna, que ainda há consulta(s) ativa(s) DO MESMO PACIENTE.
    # Escopado por patient_id de propósito: um contato pode gerenciar vários
    # pacientes (mãe que marca para si, o filho e a filha) e cancelar a consulta
    # de um NÃO deve sinalizar as consultas dos outros como "pendentes de cancelar".
    sibling_note = ""
    try:
        if _this_patient_id is not None:
            _others_res = await client.from_("appointments").select(
                "appointment_id, start_time"
            ).eq("patient_id", _this_patient_id).in_(
                "status", ["scheduled", "pending_reschedule"]
            ).execute()
            _remaining = [
                a for a in (_others_res.data or [])
                if a.get("appointment_id") != appointment_id
            ]
            if _remaining:
                _lines = []
                for _o in _remaining:
                    _st = _o.get("start_time")
                    _fmt = (
                        datetime.fromisoformat(_st).astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
                        if _st else "horário não disponível"
                    )
                    _lines.append(f"- {_fmt} (ID: {_o.get('appointment_id')})")
                sibling_note = (
                    "\n\n[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Este paciente/contato "
                    f"ainda tem {len(_remaining)} consulta(s) ATIVA(S):\n" + "\n".join(_lines) +
                    "\nSe o pedido foi cancelar MAIS DE UMA consulta (ex: primeira consulta "
                    "infantil dividida em duas partes — dois agendamentos que coexistem), chame "
                    "cancel_appointment para CADA uma dessas antes de responder. NÃO diga que "
                    "'as consultas foram canceladas' enquanto ainda houver consulta ativa listada acima."
                )
    except Exception:
        # Nunca falhar o cancelamento por causa do aviso auxiliar.
        logging.getLogger(__name__).exception("cancel_appointment: falha ao checar consultas irmãs")

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
        return "FEE_PRESERVED\nSlot liberado e taxa de reserva preservada para remarcação futura. ✅" + sibling_note
    else:
        await _notify_clinic(
            f"Agendamento cancelado! ❌\n"
            f"Paciente: {patient_name}\n"
            f"Data e horário: {formatted_old}\n"
            f"Médico(a): {doctor_label}",
            phone=phone,
            subject=f"Agendamento cancelado — {patient_name}",
        )
        return "Consulta cancelada com sucesso. ✅" + sibling_note


@tool
async def cancel_all_appointments(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    preserve_fee: bool = False,
) -> str:
    """Cancela DE UMA VEZ as duas partes da primeira consulta de UM paciente. Passe o appointment_id
    de QUALQUER uma das consultas do paciente — a ferramenta cancela todas as consultas ativas
    (scheduled/pending_reschedule) que compartilham o MESMO paciente.

    Use quando o paciente pede para cancelar/desmarcar TODAS as consultas dele e há mais de uma ativa —
    tipicamente a primeira consulta infantil, dividida em duas partes (responsáveis + paciente), que
    são dois agendamentos que coexistem. Assim você não corre o risco de cancelar só uma e deixar a
    outra ativa. Se houver apenas UMA consulta ativa, prefira cancel_appointment.

    IMPORTANTE: escopo é por PACIENTE, não pelo contato. Se o contato gerencia vários pacientes e
    quer cancelar as consultas de mais de um, chame esta ferramenta uma vez para cada paciente (com
    um appointment_id de cada). Nunca presuma que "cancelar tudo" inclui outros pacientes.

    preserve_fee=True: mantém a taxa já paga para remarcação futura (status → pending_reschedule);
    preserve_fee=False (padrão): cancelamento definitivo (status → canceled). O reembolso, quando
    cabível, continua sendo tratado à parte (register_refund_request), igual a cancel_appointment.
    """
    from app.google_calendar import cancel_event

    client = await get_supabase()
    phone = config["configurable"]["phone"]
    users = await get_users_by_phone(phone)
    contact_patient_ids = [u["id"] for u in users]
    name_by_id = {
        u["id"]: (u.get("patient_name") or u.get("name") or "Paciente") for u in users
    }

    # Resolve o paciente a partir do appointment_id informado e valida que pertence
    # a este contato (impede cancelar consulta de outro número).
    ref = await client.from_("appointments").select(
        "patient_id"
    ).eq("appointment_id", appointment_id).maybe_single().execute()
    target_patient_id = (ref.data or {}).get("patient_id")
    if target_patient_id is None or target_patient_id not in contact_patient_ids:
        return "ID de agendamento inválido para este contato."

    res = await client.from_("appointments").select(
        "appointment_id, start_time, booking_fee_paid_at, doctor_id, patient_id, status"
    ).eq("patient_id", target_patient_id).in_(
        "status", ["scheduled", "pending_reschedule"]
    ).order("start_time").execute()
    appts = res.data or []
    if not appts:
        return "Não há consultas ativas para cancelar."

    canceled = []
    for a in appts:
        aid = a["appointment_id"]
        doctor_key = DOCTOR_NAMES.get(a.get("doctor_id"))
        calendar_id = await _get_doctor_calendar_id(doctor_key) if doctor_key else None
        # Libera o slot no Calendar (quando o médico é conhecido)
        if calendar_id:
            await cancel_event(calendar_id, aid)
        fee_was_paid = bool(a.get("booking_fee_paid_at"))
        new_status = "pending_reschedule" if (preserve_fee and fee_was_paid) else "canceled"
        await client.from_("appointments").update({
            "status": new_status,
            "updated_at": datetime.now(TZ).isoformat(),
        }).eq("appointment_id", aid).execute()
        await log_event(
            "appointment_canceled", phone,
            {"appointment_id": aid, "preserve_fee": preserve_fee, "batch": True},
        )
        st = a.get("start_time")
        fmt = (
            datetime.fromisoformat(st).astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
            if st else "horário não disponível"
        )
        doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
        canceled.append({
            "patient": name_by_id.get(a.get("patient_id"), "Paciente"),
            "when": fmt,
            "doctor": doctor_label,
            "status": new_status,
        })

    # Notifica a clínica uma única vez, com o resumo do lote
    lines = "\n".join(f"- {c['patient']} — {c['when']} — {c['doctor']}" for c in canceled)
    any_preserved = any(c["status"] == "pending_reschedule" for c in canceled)
    await _notify_clinic(
        f"Cancelamento em lote ❌ ({len(canceled)} consulta(s)):\n{lines}"
        + ("\nTaxa(s) de reserva preservada(s) para remarcação futura." if any_preserved else ""),
        phone=phone,
        subject=f"Cancelamento em lote — {canceled[0]['patient']}",
    )

    body = "\n".join(f"- {c['when']} ({c['doctor']})" for c in canceled)
    if any_preserved:
        return (
            f"FEE_PRESERVED\n{len(canceled)} consulta(s) liberada(s), taxa preservada para "
            f"remarcação futura. ✅\n{body}"
        )
    return f"{len(canceled)} consulta(s) cancelada(s) com sucesso. ✅\n{body}"


@tool
async def mark_reschedule_in_progress(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    initiated_by: Literal["patient", "clinic"] | None = None,
) -> str:
    """Marca que um reagendamento está em andamento para esta consulta.
    Chame ANTES de get_available_slots quando o paciente pedir para remarcar uma consulta existente.
    Isso registra o timestamp de início para que o sistema possa liberar o slot automaticamente
    caso o paciente não confirme o novo horário em 1 hora.

    initiated_by: só relevante quando a instrução vem de nota privada da atendente (silent_mode).
      Use "patient" se a nota deixar claro que é a pedido do paciente (ex: paciente ligou pedindo
      para remarcar); use "clinic" se for por iniciativa da clínica/médico (ex: médico precisou
      ajustar a agenda). Se a nota da atendente não deixar isso claro, NÃO chame esta ferramenta
      ainda — pergunte antes, em nota privada, qual dos dois casos se aplica. Fora do silent_mode
      (paciente conversando diretamente), não informe este parâmetro.
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

    appt_status = appt.data.get("status")
    if appt_status not in ("scheduled", "pending_reschedule"):
        if appt_status == "canceled":
            return (
                "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Esta consulta já foi CANCELADA — "
                "a vaga já foi liberada, não está mais reservada nem pendente de pagamento. "
                "NÃO diga ao paciente que a consulta \"ainda está reservada\" ou qualquer coisa "
                "parecida — isso seria falso. Informe que essa consulta não está mais ativa e "
                "ofereça um NOVO agendamento: chame get_available_slots e, ao confirmar o novo "
                "horário, confirm_appointment (nova consulta, nova taxa de reserva de R$ 100,00)."
            )
        return (
            f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Esta consulta está com status "
            f"\"{appt_status}\" e não permite reagendamento. NÃO afirme que a consulta ainda "
            "está reservada ou pendente de pagamento se isso não corresponder à realidade. "
            "Informe o paciente da situação real desta consulta e oriente o próximo passo "
            "adequado (ex: se \"completed\", a consulta já ocorreu — ofereça um novo "
            "agendamento se for o caso)."
        )

    # Quando a instrução vem de nota privada da atendente, é preciso saber se a
    # remarcação é a pedido do paciente ou por iniciativa da clínica/médico —
    # isso decide se ela conta como a remarcação gratuita do paciente. Sem essa
    # informação, pergunte antes de prosseguir em vez de assumir um lado.
    if state.get("silent_mode"):
        if initiated_by is None:
            return (
                "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Antes de prosseguir, pergunte à "
                "atendente, em nota privada, se esta remarcação é \"a pedido do paciente\" ou "
                "\"pela clínica/médico\", e chame mark_reschedule_in_progress novamente informando "
                "initiated_by. Explique que: \"a pedido do paciente\" conta como a remarcação do "
                "paciente (pode gerar cobrança de nova taxa se ele já tiver remarcado antes); "
                "\"pela clínica\" não conta como remarcação do paciente e não gera cobrança."
            )
        effective_initiated_by = initiated_by
    else:
        effective_initiated_by = "patient"

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
        # Reagendamentos marcados como iniciativa da clínica (reschedule_initiated_by
        # = "clinic") não contam aqui — eventos antigos sem essa marcação são
        # tratados como remarcação do paciente (era o único fluxo antes desta mudança).
        count_res = await client.from_("events").select("id", count="exact") \
            .eq("phone", phone_clean).eq("event_type", "appointment_rescheduled") \
            .or_("metadata->>initiated_by.is.null,metadata->>initiated_by.eq.patient").execute()
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
        "reschedule_initiated_by": effective_initiated_by,
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
async def keep_original_appointment(
    appointment_id: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Reverte uma remarcação em andamento quando o paciente DESISTE de remarcar e quer
    MANTER a consulta no horário original. appointment_id é o Google Calendar event ID da
    consulta marcada com remarcação pendente (status pending_reschedule).
    Verifica se o horário original ainda está livre no Calendar: se estiver, recria o evento
    e reativa a consulta (status volta para scheduled, taxa de reserva já paga preservada);
    se o horário já tiver sido ocupado por outro paciente, retorna instrução para oferecer
    novos horários. NUNCA diga ao paciente que a consulta "está mantida" sem antes chamar
    esta ferramenta — sem ela o slot continua liberado e à venda para outros pacientes.
    """
    client = await get_supabase()
    phone = config["configurable"]["phone"]

    users = await get_users_by_phone(phone)
    user_ids = [u["id"] for u in users]
    appt = await client.from_("appointments").select(
        "appointment_id, status, patient_id, start_time, end_time, modality, patients(name, email)"
    ).eq("appointment_id", appointment_id).maybe_single().execute()

    if not appt.data or appt.data.get("patient_id") not in user_ids:
        return "ID de agendamento inválido para este paciente."

    appt_status = appt.data.get("status")
    if appt_status == "scheduled":
        return (
            "A consulta já está ativa no horário original — não há remarcação pendente a "
            "reverter. Pode confirmar ao paciente que está tudo certo com o agendamento."
        )
    if appt_status != "pending_reschedule":
        if appt_status == "canceled":
            return (
                "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Esta consulta já foi CANCELADA — "
                "a vaga foi liberada e não é possível \"mantê-la\". NÃO diga ao paciente que a "
                "consulta foi mantida. Informe que ela não está mais ativa e ofereça um NOVO "
                "agendamento: get_available_slots e, ao confirmar o horário, confirm_appointment "
                "(nova taxa de reserva de R$ 100,00)."
            )
        return (
            f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Esta consulta está com status "
            f"\"{appt_status}\" e não há remarcação pendente a reverter. NÃO afirme que a "
            "consulta foi mantida — informe o paciente da situação real e oriente o próximo "
            "passo adequado."
        )

    start_time_str = appt.data.get("start_time")
    if not start_time_str:
        return "Não foi possível obter a data e hora da consulta original."
    # start_time vem do banco em UTC — .astimezone(TZ) converte para o horário real de Recife.
    start = datetime.fromisoformat(start_time_str).astimezone(TZ)
    if start <= datetime.now(TZ):
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] O horário original desta consulta "
            f"({start.strftime('%d/%m/%Y às %H:%M')}) já passou — não é possível mantê-lo. "
            "Avise o paciente com empatia e siga o fluxo de remarcação: get_available_slots "
            "para buscar novos horários e reschedule_appointment ao confirmar (a taxa já "
            "registrada segue preservada)."
        )

    slot_minutes = 60
    if appt.data.get("end_time"):
        end = datetime.fromisoformat(appt.data["end_time"]).astimezone(TZ)
        slot_minutes = int((end - start).total_seconds() / 60)

    doctor = await _resolve_doctor(state, config)
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    # O evento original foi apagado quando a remarcação começou — o slot pode ter sido
    # vendido a outro paciente nesse meio-tempo. Confere no Calendar antes de recriar.
    from app.google_calendar import _get_busy, _credentials, create_event
    from googleapiclient.discovery import build as _build
    try:
        _creds = _credentials()
        _service = _build("calendar", "v3", credentials=_creds)
        loop = asyncio.get_running_loop()
        busy = await loop.run_in_executor(None, _get_busy, _service, calendar_id, start, start + timedelta(minutes=slot_minutes))
    except Exception:
        busy = []  # If check fails, proceed — create_event will fail anyway if the API is down
    if busy:
        return (
            f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] O horário original "
            f"({start.strftime('%d/%m/%Y às %H:%M')}) já foi ocupado por outro agendamento — "
            "NÃO diga ao paciente que a consulta foi mantida. Avise com empatia que o horário "
            "foi preenchido nesse meio-tempo e chame get_available_slots para oferecer novos "
            "horários (a remarcação segue pendente e a taxa já registrada segue preservada)."
        )

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor, "médico(a)")
    _appt_patient = appt.data.get("patients") or {}
    patient_name = _appt_patient.get("name") or state.get("patient_name") or state.get("user_name") or "Paciente"
    patient_email = _appt_patient.get("email") or state.get("patient_email") or ""
    patient_age = state.get("patient_age") or 99
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)

    try:
        new_event_id = await create_event(
            calendar_id=calendar_id,
            start=start,
            slot_minutes=slot_minutes,
            patient_name=patient_name,
            doctor_name=doctor_label,
            is_minor_first=is_minor_first,
            modality=appt.data.get("modality") or "",
            patient_email=patient_email,
            patient_number=phone,
        )
    except Exception as e:
        logger.error("KEEP_ORIGINAL create_event FAILED appt=%s error=%s", appointment_id, e, exc_info=True)
        return f"Não foi possível recriar o evento no Google Calendar. Erro: {e}"

    # Só status/evento/flags de remarcação mudam — booking_fee_paid_at/paid_at ficam intactos.
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "reschedule_requested_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", appointment_id).execute()

    await log_event("reschedule_reverted", phone, {
        "appointment_id": appointment_id,
        "new_event_id": new_event_id,
    })

    _weekday = _WEEKDAY_LABELS_PT.get(start.weekday(), "")
    formatted = f"{_weekday}, {start.strftime('%d/%m/%Y às %H:%M')}" if _weekday else start.strftime("%d/%m/%Y às %H:%M")

    await _notify_clinic(
        f"Remarcação desfeita — consulta mantida ✅\n"
        f"Paciente: {patient_name}\n"
        f"Horário mantido: {formatted}\n"
        f"Médico(a): {doctor_label}",
        phone=phone,
        subject=f"Remarcação desfeita — {patient_name}",
    )

    return (
        f"Consulta mantida no horário original! ✅\n"
        f"{doctor_label} — {formatted}\nID: {new_event_id}"
    )


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

    # start_time vem do banco em UTC — .astimezone(TZ) converte, .replace(tzinfo=TZ)
    # reescreveria o evento 3h adiante do horário real da consulta.
    start = datetime.fromisoformat(start_time_str).astimezone(TZ)
    slot_duration = 60  # Assume 1 hour by default
    if appt_result.data.get("end_time"):
        end = datetime.fromisoformat(appt_result.data["end_time"]).astimezone(TZ)
        slot_duration = int((end - start).total_seconds() / 60)

    # Mesma precedência de confirm_appointment/reschedule_appointment: restrição
    # cadastral e grade do médico valem mais que a preferência do momento.
    from app.google_calendar import get_modality_for_slot
    restriction = state.get("modality_restriction")
    if restriction in ("online", "presencial") and restriction != new_modality:
        return (
            f"Conforme o cadastro, as consultas deste paciente são {restriction}. "
            "Não é possível alterar a modalidade por aqui — transfira para a atendente se o paciente insistir."
        )
    if get_modality_for_slot(doctor, start) == "online" and new_modality == "presencial":
        return (
            "Este horário é exclusivamente online na agenda do médico — não é possível "
            "torná-lo presencial. Ofereça manter online ou remarcar para um horário presencial."
        )

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
    confirmed_by_patient: bool = True,
    force_encaixe: bool = False,
) -> str:
    """
    Remarca uma consulta existente para um novo horário.
    appointment_id é o Google Calendar event ID.
    force_encaixe: apenas com solicitação explícita da atendente (silent_mode). Use quando
      o encaixe pedido cai sobre um paciente que já tem consulta — a instrução interna do
      confirm_appointment manda remarcar direto para o horário do encaixe. Pula o busy-check
      (encaixe pode sobrepor de propósito) e aplica a regra de duração do encaixe da Bruna.
    new_slot_datetime deve estar no formato ISO 8601 em HORÁRIO LOCAL DE RECIFE (UTC-3),
    exatamente como exibido ao paciente — NUNCA converta para UTC antes de passar.
    modality: modalidade do novo horário — "online" ou "presencial" (se aplicável).
    NÃO defina confirmed_by_patient — deixe o valor padrão (True). Só chame esta ferramenta
      quando a nova data/horário já for definitiva (seja porque o paciente escolheu no chat,
      seja porque a atendente definiu via instrução/nota privada). Este parâmetro é reservado
      para scripts administrativos internos, nunca para uso pela Eva.
    Se a data/hora da consulta NÃO for mudar e o paciente só quiser trocar entre
      online e presencial, use change_modality em vez desta ferramenta — reschedule_appointment
      conta como o reagendamento gratuito do paciente (política de 1 remarcação), então usá-la
      só para trocar a modalidade consome esse benefício indevidamente.
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

    # force_encaixe só vale por instrução da atendente (silent_mode). Fora dele, ignora —
    # o paciente não pode se auto-encaixar num horário fora da grade (espelha confirm_appointment).
    if force_encaixe and not state.get("silent_mode"):
        force_encaixe = False
        logger.warning("reschedule_appointment: force_encaixe=True rejeitado — fora do silent_mode")

    # Encaixe da Dra. Bruna começando a :20 termina no topo da hora (40min), pra não
    # bloquear o slot regular da hora seguinte (mesma regra do confirm_appointment).
    if force_encaixe and doctor == "bruna" and new_start.minute == 20:
        slot_duration_minutes = 40

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
    appt_result = await client.from_("appointments").select("start_time, patient_id, patients(name), reschedule_initiated_by").eq("appointment_id", appointment_id).maybe_single().execute()

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

    old_dt = None
    if old_start_time:
        try:
            old_dt = datetime.fromisoformat(old_start_time).astimezone(TZ)
        except ValueError:
            old_dt = None

    # Guard: check Google Calendar for conflicts on the NEW slot before writing it.
    # confirm_appointment already had this check, but reschedule_appointment didn't —
    # a stale slot offer confirmed after someone else took that time slipped through
    # and double-booked the doctor (caso Raynner/Bernardo, 23/07/2026 19h, Dr. Júlio).
    # Skipped when old_dt == new_start (pure modality change, no time actually moves —
    # the appointment's own event already occupies that slot).
    # Skipped também no encaixe (force_encaixe): a atendente pediu explicitamente para
    # encaixar sobre um horário fora da grade, que pode sobrepor de propósito.
    if confirmed_by_patient and old_dt != new_start and not force_encaixe:
        from app.google_calendar import _get_busy, _credentials
        from googleapiclient.discovery import build as _build
        new_end_check = new_start + timedelta(minutes=slot_duration_minutes)
        try:
            _creds = _credentials()
            _service = _build("calendar", "v3", credentials=_creds)
            loop = asyncio.get_running_loop()
            busy = await loop.run_in_executor(None, _get_busy, _service, calendar_id, new_start, new_end_check)
            if busy:
                return (
                    f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
                    f"Este horário ({new_start.strftime('%d/%m/%Y às %H:%M')}) já está ocupado por outro agendamento. "
                    "Avise o paciente com empatia e chame get_available_slots para buscar outro horário disponível."
                )
        except Exception:
            pass  # If check fails, proceed anyway — better to double-book than block

    # Only create/update Google Calendar event if patient confirmed
    if confirmed_by_patient:
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
        "updated_at": datetime.now(TZ).isoformat(),
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
    }

    # Only mark as scheduled and clear reschedule flag if patient confirmed
    if confirmed_by_patient:
        reschedule_update["status"] = "scheduled"
        reschedule_update["reschedule_requested_at"] = None
    else:
        # Keep as pending_reschedule if this is just an admin adjustment
        reschedule_update["status"] = "pending_reschedule"

    if effective_modality:
        reschedule_update["modality"] = effective_modality
    await client.from_("appointments").update(reschedule_update).eq("appointment_id", appointment_id).execute()

    _weekday_new = _WEEKDAY_LABELS_PT.get(new_start.weekday(), "")
    formatted_new = f"{_weekday_new}, {new_start.strftime('%d/%m/%Y às %H:%M')}" if _weekday_new else new_start.strftime("%d/%m/%Y às %H:%M")

    if old_dt is not None:
        formatted_old = old_dt.strftime("%d/%m/%Y às %H:%M")
    else:
        formatted_old = "horário não disponível"

    # Se a data/hora não mudou, isto é uma troca de modalidade (ex: chamada por
    # engano em vez de change_modality) — não conta como o reagendamento gratuito
    # do paciente, senão consome a política de "1 remarcação" sem o paciente ter
    # de fato remarcado nada.
    if old_dt is not None and old_dt == new_start:
        await log_event("modality_changed", phone, {
            "appointment_id": appointment_id,
            "new_modality": effective_modality,
        })
    else:
        await log_event("appointment_rescheduled", phone, {
            "appointment_id": appointment_id,
            "new_datetime": new_slot_datetime,
            "initiated_by": (appt_result.data or {}).get("reschedule_initiated_by")
                or ("clinic" if state.get("silent_mode") else "patient"),
        })

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
    document_type: Literal["nota_fiscal", "recibo", "laudo", "exame", "relatorio", "receita", "declaracao", "requisicao", "atestado"],
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
        "requisicao": "Requisição", "atestado": "Atestado",
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
async def request_external_contact(
    third_party_role: str,
    third_party_name: str,
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    third_party_contact: str = "",
) -> str:
    """Registra um pedido do paciente para que o médico entre em contato com um terceiro
    externo ligado ao cuidado do paciente (psicólogo, terapeuta, outro médico, escola etc.)
    antes ou em torno de uma consulta. Chame na primeira vez que o paciente pedir isso.
    """
    from app.email_sender import send_external_contact_request_email

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or ""
    if not patient_name:
        _u = await get_user_by_phone(phone)
        patient_name = (_u or {}).get("patient_name") or (_u or {}).get("name") or "Paciente"
    patient_age = state.get("patient_age")
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    client = await get_supabase()

    # Fetch doctor email from doctors table (agenda_id = email)
    doctor_email = ""
    if doctor_id:
        result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        doctor_email = result.data.get("agenda_id", "") if result.data else ""

    # Build content string for human-readable summary
    content = f"Contato com {third_party_role.lower()}: {third_party_name}"
    if reason:
        content += f" — {reason}"

    # Insert row into requests table
    await client.from_("requests").insert({
        "type": "contato_terceiro",
        "phone": phone,
        "patient_name": patient_name,
        "doctor_id": doctor_id,
        "content": content,
        "metadata": {
            "third_party_role": third_party_role,
            "third_party_name": third_party_name,
            "third_party_contact": third_party_contact,
        },
    }).execute()

    await log_event("external_contact_requested", phone, {
        "third_party_role": third_party_role,
        "third_party_name": third_party_name,
        "reason": reason,
        "patient_name": patient_name,
    })

    # Send email to doctor — fire-and-forget
    try:
        await send_external_contact_request_email(
            doctor_key=doctor_key,
            doctor_email=doctor_email,
            patient_name=patient_name,
            patient_age=patient_age,
            phone=phone,
            third_party_role=third_party_role,
            third_party_name=third_party_name,
            third_party_contact=third_party_contact,
            reason=reason,
        )
    except Exception:
        pass

    # Notify clinic
    phone_clean = phone.replace("@s.whatsapp.net", "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
    notify_msg = (
        f"📞 Pedido de contato com {third_party_role}\n"
        f"Paciente: {patient_name}\n"
        f"Médico(a): {doctor_label}\n"
        f"Terceiro: {third_party_name}"
    )
    if third_party_contact:
        notify_msg += f"\nContato do terceiro: {third_party_contact}"
    if reason:
        notify_msg += f"\nMotivo: {reason}"
    await _notify_clinic(notify_msg, phone=phone_clean, subject=f"Pedido de contato — {patient_name}")

    return (
        f"Pedido registrado! ✅\n"
        f"Encaminhamos para o {doctor_label} para que entre em contato com {third_party_name}."
    )


@tool
async def nudge_external_contact(
    patient_message: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Notifica o médico por e-mail quando o paciente cobra sobre um pedido de contato com
    terceiro externo (psicólogo, terapeuta etc.) já registrado anteriormente.
    Chame SOMENTE quando o paciente reforçar/cobrar sobre um pedido já feito
    (ex: 'o Dr. Júlio ainda não falou com a psicóloga'). NÃO use para criar um pedido novo.
    """
    from app.email_sender import send_external_contact_nudge_email

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name") or "Paciente"
    patient_age = state.get("patient_age")
    patient_email = state.get("patient_email") or ""
    doctor_key = state.get("preferred_doctor", "")
    doctor_id = DOCTOR_IDS.get(doctor_key)

    client = await get_supabase()

    # Find most recent external contact request for this patient
    phone_clean = phone.replace("@s.whatsapp.net", "")
    requests = await client.from_("requests").select("*").eq("phone", phone).eq(
        "type", "contato_terceiro"
    ).order("created_at", desc=True).limit(1).execute()

    if not requests.data:
        return "Não encontramos um pedido anterior de contato com terceiro. Você gostaria de fazer um novo pedido?"

    request = requests.data[0]
    created_at = request.get("created_at", "data não registrada")
    metadata = request.get("metadata") or {}
    third_party_name = metadata.get("third_party_name", "")
    third_party_role = metadata.get("third_party_role", "")

    # Fetch doctor email
    doctor_email = ""
    if doctor_id:
        res = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        doctor_email = res.data.get("agenda_id", "") if res.data else ""

    try:
        await send_external_contact_nudge_email(
            doctor_key=doctor_key,
            doctor_email=doctor_email,
            patient_name=patient_name,
            patient_age=patient_age,
            phone=phone_clean,
            patient_email=patient_email,
            third_party_name=third_party_name,
            third_party_role=third_party_role,
            patient_message=patient_message,
            created_at=created_at,
        )
    except Exception:
        logger.exception("nudge_external_contact: email failed phone=%s", phone)

    await log_event("external_contact_nudge_sent", phone_clean, {
        "third_party_name": third_party_name,
        "third_party_role": third_party_role,
        "patient_name": patient_name,
        "patient_message": patient_message,
    })

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")
    return (
        f"Aviso enviado! ✅\n"
        f"Encaminhamos sua cobrança para o {doctor_label} sobre o contato com {third_party_name}."
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

    # Idempotência: primeiro contato a confirmar vence. Quando vários responsáveis
    # (ex.: pai e mãe) recebem o lembrete, o segundo a confirmar não regrava nem
    # loga de novo.
    existing = (
        await client.from_("appointments")
        .select("confirmed_at, status")
        .eq("appointment_id", appointment_id)
        .limit(1)
        .execute()
    )
    rows = existing.data or []

    # Guarda contra appointment_id inexistente. O UPDATE abaixo é um no-op quando
    # o ID não casa nenhuma linha, e antes desta guarda a tool devolvia
    # "Presença confirmada! ✅" mesmo assim — o LLM então enviava a mensagem de
    # confirmação (com endereço da clínica) para uma consulta que não existe.
    # Caso Sayonara Lira (01/08/2026): sem nenhuma consulta agendada, a Eva
    # respondeu a um "Confirmado" chamando confirm_attendance com
    # 'bruna-20260802T1100' — o formato de SLOT LIVRE devolvido por
    # get_available_slots, não o ID de evento do Google Calendar.
    if not rows:
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Não existe nenhuma consulta "
            f"com o ID '{appointment_id}'. NÃO confirme presença, NÃO diga "
            '"presença confirmada" e NÃO envie o endereço da clínica. '
            'Use APENAS um appointment_id que esteja listado em "Consultas agendadas" '
            "no cabeçalho deste prompt — nunca invente um ID nem use o ID de um "
            "horário livre. Se não houver nenhuma consulta agendada listada, diga ao "
            "paciente que não encontrou consulta marcada e pergunte se ele deseja agendar."
        )

    status = rows[0].get("status")
    if status in ("canceled", "completed"):
        label = "cancelada" if status == "canceled" else "já realizada"
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] A consulta "
            f"'{appointment_id}' está {label}. NÃO confirme presença, NÃO diga "
            '"presença confirmada" e NÃO envie o endereço da clínica. '
            "Verifique se há outra consulta futura em \"Consultas agendadas\"; se não "
            "houver, diga ao paciente que não encontrou consulta marcada e pergunte se "
            "ele deseja agendar."
        )

    if rows[0].get("confirmed_at"):
        # A resposta NÃO pode ser igual à da primeira confirmação: o texto de
        # confirmação é um template verbatim no prompt (inclusive o bloco de
        # endereço), então repeti-lo produz uma mensagem byte a byte idêntica à
        # anterior. Caso Dr. Paulo Diniz (28/07/2026): "Bom dia" confirmou a
        # presença e o "Sim" seguinte fez a Eva reenviar os mesmos 203 caracteres.
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] A presença JÁ estava "
            "confirmada anteriormente. NÃO repita a mensagem de confirmação nem o "
            "endereço da clínica. Responda apenas com uma frase curta e acolhedora "
            'confirmando que já está tudo certo (ex.: "Tudo certo, já está confirmado! 😊").'
        )

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
    """Return the expected full payment amount (with R$50 PIX discount).

    price_override: patient's custom card price (patients.custom_price) — the R$50
        PIX/cash discount still applies on top of it, same as standard pricing.
        Exception: 0 means a courtesy (free) consultation, returned as-is.
    consultation_type: value stored in appointments.consultation_type at booking time.
        'primeira_consulta' → first visit pricing (higher)
        'acompanhamento' or None → follow-up pricing (default for unknown)
    """
    if price_override is not None:
        return price_override - 50 if price_override else price_override
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


def _parse_brl_amount(raw: str) -> float:
    """Parse a monetary string that may use Brazilian (1.234,56) or plain/US
    (1234.56 / 1234) formatting into a float. Returns 0.0 if unparseable.

    A naive "strip dots, comma→dot" parse (Brazilian-only) silently mangles a
    value like "100.00" into 10000.0 whenever the amount arrives with a dot
    decimal separator (e.g. the LLM copies it verbatim from an English-formatted
    receipt), which can wrongly classify a booking fee as a full payment.
    """
    cleaned = (raw or "").replace("R$", "").replace(" ", "").strip()
    if not cleaned:
        return 0.0
    has_comma = "," in cleaned
    has_dot = "." in cleaned
    try:
        if has_comma and has_dot:
            # Whichever separator appears last is the decimal separator.
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif has_comma:
            cleaned = cleaned.replace(",", ".")
        elif has_dot:
            # A lone dot followed by exactly 2 digits is a decimal separator
            # (e.g. "100.00"); anything else is treated as a thousands
            # separator (e.g. "1.200" → 1200), matching Brazilian convention.
            _, _sep, frac = cleaned.rpartition(".")
            if len(frac) != 2:
                cleaned = cleaned.replace(".", "")
        return float(cleaned)
    except ValueError:
        return 0.0


# Fragmento-sentinela do branch "horário já ocupado" da reativação por comprovante
# (register_payment abaixo). patient_agent_node usa este texto para detectar o
# resultado e enviá-lo ao paciente VERBATIM, sem re-síntese pela LLM: sobre
# disponibilidade, a conversa não pode vencer a tool — a LLM já reescreveu esse
# resultado como "Vou seguir com a reativação da consulta... conforme combinado"
# (caso Ricardo José Vieira Cunha Filho, contato 5581988912861, 10/08/2026).
REACTIVATION_SLOT_TAKEN_MARKER = "não está mais disponível"


def _payment_disambiguation_prompt(context: str, names: str) -> str:
    """Retorno de register_payment quando o paciente do comprovante é ambíguo.

    Devolver só a pergunta ("para qual deles é o comprovante?") já custou caro:
    Eva perguntava ao paciente, recebia a resposta e escrevia "taxa recebida,
    consulta garantida" sem nunca chamar a tool de novo — o pagamento ficava
    sem registro e o cron de cobrança disparava horas depois (caso Juliana,
    5581981845995, 04/08/2026). Por isso o retorno é instrução interna e diz,
    em vez de perguntar, o que precisa acontecer na próxima chamada.
    """
    return (
        f"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] {context} "
        "NADA foi registrado: nenhum pagamento foi salvo e nenhuma vaga foi garantida. "
        f"Pergunte ao paciente para qual deles é o comprovante (cite os nomes: {names}) e, "
        "assim que ele responder, chame register_payment DE NOVO com "
        "patient_name_override=[nome completo do paciente escolhido], mantendo amount, "
        "drive_link e image_description da mensagem original. "
        "Vale como resposta qualquer forma de identificar o paciente — o nome, o número da "
        "opção, ou um 'sim' confirmando o nome que você citou numa pergunta fechada. "
        "NUNCA diga que o comprovante foi recebido/registrado, nem que a consulta está "
        "garantida, antes de uma chamada de register_payment retornar sucesso."
    )


_CLINIC_PIX_DIGITS = re.sub(r"\D", "", CORRECT_PIX_KEY)  # "42006848000178"


def _receipt_destination_is_foreign(image_description: str) -> bool:
    """True somente quando o comprovante mostra INEQUIVOCAMENTE uma chave de
    destino diferente da chave PIX da clínica (CORRECT_PIX_KEY).

    Fail-open: retorna False em qualquer caso ambíguo — texto vazio, sem token de
    chave legível, ou chave mascarada/curta (< 11 dígitos). Só barra quando há um
    token de chave/CPF/CNPJ com >= 11 dígitos que não casa (nem por substring) com
    o CNPJ da clínica.
    """
    if not image_description:
        return False

    full_digits = re.sub(r"\D", "", image_description)
    # CNPJ da clínica aparece em qualquer lugar do texto → é da clínica.
    if _CLINIC_PIX_DIGITS in full_digits:
        return False

    # Extrai o token de destino: trecho após "chave PIX" / "CPF" / "CNPJ" até
    # a próxima vírgula, ponto-e-vírgula ou fim de linha.
    m = re.search(
        r"(?:chave\s*pix|cpf\s*/?\s*cnpj|cnpj|cpf)\s*[:\-]?\s*([^,;\n]+)",
        image_description,
        re.IGNORECASE,
    )
    if not m:
        return False

    dest_digits = re.sub(r"\D", "", m.group(1))
    if len(dest_digits) < 11:
        return False  # mascarada/curta → fail-open

    if _CLINIC_PIX_DIGITS in dest_digits or dest_digits in _CLINIC_PIX_DIGITS:
        return False  # casa (inclusive máscara que é substring) → clínica

    return True


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
    sender_confirmed_patient: bool = False,
) -> str:
    """
    Registra um comprovante de pagamento PIX na planilha.
    amount: valor pago (ex: "100,00") ou "?" se não identificado.
    drive_link: URL extraída da tag [drive_link:URL]. Passe "" se não houver.
    payment_method: para presencial sem imagem — "cartao_credito", "cartao_debito" ou "dinheiro".
    image_description: texto completo da descrição da imagem.
    patient_name_override: quando o remetente informar nome de paciente diferente do estado.
      Se sem vínculo cadastrado, a tool pede confirmação — chame novamente com
      sender_confirmed_patient=True após confirmação explícita do remetente.
    """
    import logging as _log
    import re as _re
    _logger = _log.getLogger(__name__)

    from app.google_sheets import append_payment_receipt

    # ── Guard: comprovante para chave que não é da clínica ─────────────────────
    # Só inspeciona quando há imagem de comprovante; pagamentos do painel/atendente
    # (is_link / payment_method, sem image_description) passam direto.
    if image_description and not is_link and not payment_method:
        if _receipt_destination_is_foreign(image_description):
            _logger.warning(
                "REGISTER_PAYMENT blocked: destino estrangeiro | desc=%r",
                image_description[:160],
            )
            return (
                "⚠️ Esse comprovante foi para outra chave PIX, não para a da clínica. "
                f"NÃO registrei o pagamento. Peça ao paciente para conferir e refazer o "
                f"PIX para a chave {CORRECT_PIX_KEY} (CNPJ PSIQUE) e reenviar o comprovante."
            )

    phone = config["configurable"]["phone"]
    client = await get_supabase()

    # ── Sanitize / recover drive_link ─────────────────────────────────────────
    # The LLM sometimes passes the full tag "[drive_link:URL]" instead of the
    # bare URL, or passes "" when the tag WAS present but wasn't extracted.
    # Strategy:
    #  1. Strip the [drive_link:...] wrapper if the LLM passed the full tag.
    #  2. If drive_link is still empty, extract from image_description.
    #  3. If still empty, scan recent messages for it — this covers both the
    #     attendant asking (via private note) to recognize a receipt already in
    #     the conversation, AND the patient insisting "já enviei, está aqui" after
    #     the bot missed/ignored an earlier image. Never do this for is_link/
    #     payment_method payments (dashboard or "PAGAMENTO CONFIRMADO" flows) —
    #     those intentionally have no receipt image, and grabbing an older
    #     comprovante's link would misattribute the file to the wrong payment.
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
    if not drive_link and not is_link and not payment_method:
        _RECENT_MSG_WINDOW = 40
        for _msg in reversed(state.get("messages", [])[-_RECENT_MSG_WINDOW:]):
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

        candidates = user_result.data

        from app.patients import get_contact_by_phone as _gcbp, get_patients_by_contact as _gpbc
        _sender_contact = await _gcbp(phone)
        _linked_ids = {p["id"] for p in await _gpbc(_sender_contact["id"])} if _sender_contact else set()

        if len(candidates) > 1:
            # Ambiguous ilike match (e.g. "%Francisco%" matches many unrelated patients).
            # Prefer an exact case-insensitive full-name match; otherwise prefer the
            # candidate already linked (via patient_contacts) to the sender's own phone —
            # never silently pick candidates[0], which can misattribute a payment to a
            # completely unrelated patient.
            exact = [c for c in candidates if c.get("name", "").strip().lower() == search_name.lower()]
            if len(exact) == 1:
                candidates = exact
            else:
                linked = [c for c in candidates if c["id"] in _linked_ids]
                if len(linked) == 1:
                    candidates = linked
            if len(candidates) > 1:
                names = ", ".join(c.get("name", "Paciente") for c in candidates)
                return _payment_disambiguation_prompt(
                    f"Encontrei mais de um paciente com nome parecido a '{search_name}': {names}.",
                    names,
                )

        matched = candidates[0]
        patient_name = matched.get("name", "Paciente")
        user_id = matched["id"]

        # Safeguard: a unique/exact name match is not proof of identity — the sender
        # could type a name that happens to match a different patient's registration.
        # Without a known patient_contacts link, require explicit confirmation before
        # filing the payment under this patient, to avoid silently misattributing it.
        if user_id not in _linked_ids and not sender_confirmed_patient:
            return (
                f"Encontrei o paciente '{patient_name}' pelo nome informado, mas este número "
                f"não está cadastrado como contato dele. Confirme com quem enviou que o "
                f"comprovante é realmente para '{patient_name}' antes de eu registrar o pagamento."
            )
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
            return _payment_disambiguation_prompt(
                f"Encontrei mais de um paciente neste número: {names}.", names
            )

        user_ids = [u["id"] for u in all_users]

        # No date window: the patient may settle the saldo of a consultation that
        # happened weeks/months ago. A now-15d lower bound hid that completed appt,
        # leaving seen_users empty so Eva asked "Para qual paciente é este
        # comprovante?" for a phone with a single, unambiguous patient (caso Danniela,
        # 5581991950147 — same root as the override-path double booking fee). The
        # window was never needed for disambiguation: multiple patients on one phone
        # are already caught above from get_users_by_phone, before this query runs.
        appts_result = await client.from_("appointments").select(
            "appointment_id, start_time, doctor_id, status, patients(id, name)"
        ).in_("patient_id", user_ids).in_(
            "status", ["scheduled", "completed"]
        ).order("start_time", desc=True).execute()

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
                "appointment_id, start_time, end_time, doctor_id, status, patients(id, name, birth_date)"
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
            return _payment_disambiguation_prompt(
                f"Encontrei mais de um paciente com consulta agendada neste número: {names}.",
                names,
            )

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
            # No date window: a patient may settle the saldo weeks or months after
            # the consultation. Bounding this to a recent lookback hid the completed
            # appointment carrying booking_fee_paid_at, so Eva stopped recognizing the
            # already-paid R$100 booking fee and charged it a second time (caso Danniela
            # Azevedo, 5581991950147, 2026-08-12: consult 08/07, saldo pago 12/08).
            # The paid_at guard below still blocks a duplicate on an already-settled one.
            completed_raw = await client.from_("appointments").select(_appt_fields).eq(
                "patient_id", user_id
            ).eq("status", "completed").order(
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
                        f"{REACTIVATION_SLOT_TAKEN_MARKER}. Vou verificar os próximos horários disponíveis "
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
    # new_filename is passed WITHOUT an extension — rename_file preserves whatever
    # extension the file was actually uploaded with (jpg or pdf), instead of the
    # previous hardcoded ".jpg" that mislabeled every PDF receipt. It returns the
    # resolved name (extension included), which is then handed to
    # append_payment_receipt so the comprovante link in the Pagamentos sheet displays
    # exactly the name the file has in Drive.
    _drive_rename_failed = False
    _receipt_filename = ""
    if drive_link:
        try:
            from app.google_drive import build_receipt_filename, rename_file
            # Support both /d/{id}/... and ?id={id} URL formats
            _fid_match = _re.search(r'/d/([^/?&#\s]+)', drive_link) or \
                         _re.search(r'[?&]id=([^?&#\s]+)', drive_link)
            if not _fid_match:
                raise ValueError(f"Cannot extract file_id from drive_link: {drive_link!r}")
            file_id = _fid_match.group(1)
            new_filename = build_receipt_filename(patient_name, appointment_dt, amount)
            _receipt_filename = await rename_file(file_id, new_filename)
            _logger.info("DRIVE_RENAME OK file_id=%s new_name=%s", file_id, _receipt_filename)
        except Exception:
            _logger.exception("DRIVE_RENAME FAILED drive_link=%r", drive_link)
            _drive_rename_failed = True
            _receipt_filename = ""
    # The webViewLink is keyed by file ID, not filename, so the link the clinic
    # receives below still opens the right file even if the rename below failed —
    # only the friendly filename in Drive is affected. Still worth flagging: the
    # patient-name lookup used for the file's *initial* upload name is based on the
    # message sender's phone (see app/media.py::_get_patient_name), which can differ
    # from the actual patient once register_payment resolves it here — if the
    # rename that fixes that up silently fails, nobody would otherwise notice.
    _rename_warning = (
        "\n⚠️ O arquivo no Drive não pôde ser renomeado automaticamente — "
        "confira se o nome do arquivo corresponde a este paciente/pagamento."
    ) if _drive_rename_failed else ""

    # ── Classify payment and update DB fields ─────────────────────────────────
    amount_float = _parse_brl_amount(amount)

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
        _sheets_append_failed = False
        try:
            await append_payment_receipt(
                patient_name, patient_phone, doctor_label, appointment_dt,
                amount, drive_link, payment_type="Consulta", payment_method_override="",
                receipt_filename=_receipt_filename,
            )
        except Exception:
            _logger.exception("SHEETS_APPEND FAILED patient=%s", patient_name)
            _sheets_append_failed = True
        _sheets_warning = (
            "\n⚠️ O pagamento NÃO foi registrado na planilha Pagamentos — "
            "registre manualmente."
        ) if _sheets_append_failed else ""
        await _notify_clinic(
            f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}"
            f"\nTipo: Consulta (cortesia)\nConsulta: {appointment_dt}\nLink: {drive_link}"
            f"{_rename_warning}{_sheets_warning}",
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
        if appt_already_occurred:
            payment_note = (
                f"Valor pago: R$ {amount} — taxa de reserva registrada. "
                f"A consulta já ocorreu — o saldo restante de R$ {saldo:.0f},00 (com desconto PIX) "
                f"já pode ser quitado agora."
            )
        else:
            payment_note = (
                f"Valor pago: R$ {amount} — taxa de reserva registrada. "
                f"Saldo restante para quitação no dia da consulta: R$ {saldo:.0f},00 (com desconto PIX)."
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
    _sheets_append_failed = False
    try:
        await append_payment_receipt(patient_name, patient_phone, doctor_label, appointment_dt, amount, drive_link, payment_type=payment_type, payment_method_override=_sheets_payment_method, receipt_filename=_receipt_filename)
    except Exception:
        _logger.exception("SHEETS_APPEND FAILED patient=%s", patient_name)
        _sheets_append_failed = True
    _sheets_warning = (
        "\n⚠️ O pagamento NÃO foi registrado na planilha Pagamentos — "
        "registre manualmente."
    ) if _sheets_append_failed else ""

    await _notify_clinic(
        f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}\nTipo: {payment_type}\nConsulta: {appointment_dt}\nLink: {drive_link}"
        f"{_rename_warning}{_sheets_warning}",
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
    await upsert_user(phone, {"email": email}, user_id=state.get("user_db_id"))
    await log_event("patient_email_saved", phone, {"email": email})
    return f"E-mail {email} registrado com sucesso. Agora pode prosseguir com o agendamento."


@tool
async def set_social_name(
    social_name: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Registra o nome social do paciente — o nome pelo qual ele prefere ser
    chamado, quando diferente do nome civil. Use SOMENTE quando o paciente ou
    contato mencionar espontaneamente essa preferência (ex: "pode me chamar de
    Malu", "meu nome social é..."). NUNCA pergunte isso de forma proativa.
    """
    phone = config["configurable"]["phone"]
    cleaned = _sanitize_social_name(social_name)
    if not cleaned:
        return "Não entendi o nome social informado. Pode repetir?"
    await upsert_user(phone, {"social_name": cleaned}, user_id=state.get("user_db_id"))
    await log_event("social_name_set", phone, {"social_name": cleaned})
    return f"Nome social '{cleaned}' registrado com sucesso. A partir de agora vou te chamar assim."


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


@tool
async def waive_booking_fee(
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Isenta a taxa de reserva (R$ 100,00) da consulta agendada mais próxima deste paciente.

    Use APENAS a partir de uma instrução da atendente em nota privada (silent_mode) pedindo
    para isentar/dispensar a taxa de reserva (ex: "Eva, isentar taxa de reserva", "pode
    dispensar a taxa deste paciente"). Você NUNCA decide isentar a taxa por conta própria
    fora desse contexto — é decisão exclusiva da atendente.

    Sem isso, a isenção comunicada verbalmente ao paciente não é reconhecida pelo cancelamento
    automático por falta de pagamento, que verifica apenas o banco de dados.
    """
    if not state.get("silent_mode"):
        return (
            "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Esta ferramenta só pode ser usada "
            "a partir de uma instrução da atendente em nota privada. Não isente a taxa de "
            "reserva por conta própria."
        )

    phone = config["configurable"]["phone"]
    client = await get_supabase()

    user = await get_user_by_phone(phone)
    if not user:
        return "Não encontrei cadastro para este número."

    now_iso = datetime.now(TZ).isoformat()

    appt = await client.from_("appointments").select(
        "appointment_id, start_time"
    ).eq("patient_id", user["id"]).eq("status", "scheduled").eq(
        "booking_fee_waived", False
    ).is_("booking_fee_paid_at", "null").gte("start_time", now_iso).order("start_time").limit(1).execute()

    if not appt.data:
        return "Não encontrei consulta agendada com taxa de reserva pendente para este paciente."

    appointment_id = appt.data[0]["appointment_id"]
    await client.from_("appointments").update({
        "booking_fee_waived": True,
        "booking_fee_paid_at": now_iso,
    }).eq("appointment_id", appointment_id).execute()

    await log_event("booking_fee_waived", phone, {"appointment_id": appointment_id})

    start_dt = datetime.fromisoformat(appt.data[0]["start_time"]).astimezone(TZ)
    date_str = start_dt.strftime("%d/%m/%Y às %H:%M")
    return (
        f"Taxa de reserva isentada para a consulta de {date_str}. Informe ao paciente que a "
        f"taxa de reserva foi dispensada e que não é necessário nenhum pagamento antecipado."
    )
