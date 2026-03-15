import os
from datetime import datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState

from app.uazapi import send_text
from app.database import get_supabase, log_event, upsert_user, get_user_by_phone, DOCTOR_IDS

TZ = ZoneInfo("America/Recife")

APPOINTMENT_NOTIFY_PHONE = os.getenv("APPOINTMENT_NOTIFY_PHONE", "5583998566516")


async def _notify_clinic(message: str) -> None:
    """Envia notificação de agendamento para a atendente da clínica."""
    if APPOINTMENT_NOTIFY_PHONE:
        try:
            await send_text(APPOINTMENT_NOTIFY_PHONE, message)
        except Exception:
            pass  # Não interrompe o fluxo se a notificação falhar


async def _get_doctor_calendar_id(preferred_doctor: str) -> str | None:
    """Fetch agenda_id (Google Calendar ID) for a doctor from Supabase."""
    doctor_id = DOCTOR_IDS.get(preferred_doctor)
    if not doctor_id:
        return None
    client = await get_supabase()
    result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
    return result.data.get("agenda_id") if result.data else None


@tool
async def get_available_slots(
    preferred_day: str,
    preferred_shift: Literal["manha", "tarde", "noite"],
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """
    Busca horários disponíveis no Google Calendar para o médico do paciente.
    Use slot_duration_minutes=120 para primeira consulta de paciente menor de 18 anos,
    60 para todos os outros casos.
    """
    from app.google_calendar import get_available_slots as _get_slots

    doctor = state.get("preferred_doctor", "")
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    # Dra. Bruna always uses 1h slots regardless of patient age
    if doctor == "bruna":
        slot_duration_minutes = 60

    slots = await _get_slots(
        calendar_id=calendar_id,
        preferred_day=preferred_day,
        preferred_shift=preferred_shift,
        slot_minutes=slot_duration_minutes,
        doctor_key=doctor,
    )

    if not slots:
        return f"Não há horários disponíveis para {preferred_day} no turno da {preferred_shift}. Deseja tentar outro dia ou turno?"

    lines = [f"Horários disponíveis para {preferred_day} ({preferred_shift}):"]
    for i, slot in enumerate(slots, 1):
        lines.append(f"{i}. {slot.strftime('%H:%M')}")

    return "\n".join(lines)


@tool
async def confirm_appointment(
    slot_datetime: str,
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    session_note: str = "",
) -> str:
    """
    Confirma e cria o agendamento no Google Calendar.
    slot_datetime deve estar no formato ISO 8601, ex: '2026-03-19T09:00:00'.
    session_note: use para identificar sessões separadas de menor de idade,
      ex: '1ª hora — responsáveis' ou '2ª hora — paciente'.
      Deixe vazio para consultas normais ou consultas de 2h em bloco único.
    """
    from app.google_calendar import create_event

    calendar_id = await _get_doctor_calendar_id(state.get("preferred_doctor", ""))
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    try:
        start = datetime.fromisoformat(slot_datetime).replace(tzinfo=TZ)
    except ValueError:
        return f"Formato de data inválido: {slot_datetime}. Use ISO 8601 (ex: 2026-03-19T09:00:00)."

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

    event_id = await create_event(
        calendar_id=calendar_id,
        start=start,
        slot_minutes=slot_duration_minutes,
        patient_name=patient_name,
        doctor_name=doctor_label,
        is_minor_first=is_minor_first,
        session_note=session_note,
    )

    formatted = start.strftime("%d/%m/%Y às %H:%M")
    phone = config["configurable"]["phone"]

    # Persist to appointments table
    end = start + timedelta(minutes=slot_duration_minutes)
    user = await get_user_by_phone(phone)
    client = await get_supabase()
    await client.from_("appointments").insert({
        "user_id": user["id"] if user else None,
        "doctor_id": DOCTOR_IDS.get(state.get("preferred_doctor", "")),
        "appointment_id": event_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "status": "scheduled",
    }).execute()

    await log_event("appointment_booked", phone, {
        "doctor": state.get("preferred_doctor"),
        "datetime": slot_datetime,
        "duration_minutes": slot_duration_minutes,
        "patient_name": patient_name,
        "session_note": session_note,
    })

    session_label = f" ({session_note})" if session_note else ""
    await _notify_clinic(
        f"Agendamento realizado! ✅\n"
        f"Paciente: {patient_name}{session_label}\n"
        f"Data e horário: {formatted}\n"
        f"Médico(a): {doctor_label}"
    )

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

    await _notify_clinic(
        f"Agendamento cancelado! ❌\n"
        f"Paciente: {patient_name}\n"
        f"Data e horário: {formatted_old}\n"
        f"Médico(a): {doctor_label}"
    )

    return "Consulta cancelada com sucesso. ✅"


@tool
async def reschedule_appointment(
    appointment_id: str,
    new_slot_datetime: str,
    slot_duration_minutes: Literal[60, 120],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """
    Remarca uma consulta existente para um novo horário.
    appointment_id é o Google Calendar event ID.
    new_slot_datetime deve estar no formato ISO 8601, ex: '2026-03-19T09:00:00'.
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

    # Update Google Calendar event (same event_id, new time)
    await update_event(
        calendar_id=calendar_id,
        event_id=appointment_id,
        new_start=new_start,
        slot_minutes=slot_duration_minutes,
        patient_name=patient_name,
        doctor_name=doctor_label,
        is_minor_first=is_minor_first,
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

    await _notify_clinic(
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {patient_name}\n"
        f"Horário anterior: {formatted_old}\n"
        f"Novo horário: {formatted_new}\n"
        f"Médico(a): {doctor_label}"
    )

    return f"Consulta remarcada com sucesso! ✅\n{doctor_label} — {formatted_new}"


@tool
async def request_document(
    document_type: Literal["laudo", "exame", "relatorio", "receita", "declaracao"],
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Registra uma solicitação de documento médico para o paciente."""
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    doctor_id = DOCTOR_IDS.get(state.get("preferred_doctor", ""))

    client = await get_supabase()
    await client.from_("documents").insert({
        "content": f"Solicitação de {document_type}",
        "metadata": {
            "type": document_type,
            "patient_name": patient_name,
            "doctor_id": doctor_id,
            "phone": config["configurable"]["phone"],
        },
    }).execute()

    await log_event("document_requested", config["configurable"]["phone"], {
        "document_type": document_type,
        "patient_name": patient_name,
    })
    return f"Solicitação de {document_type} registrada com sucesso. Em breve entraremos em contato."


@tool
async def transfer_to_human(
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Transfere a conversa para um atendente humano quando o bot não consegue ajudar."""
    import os
    phone = config["configurable"]["phone"]

    # Disable bot for this user
    await upsert_user(phone, {"active": False})

    # Notify the clinic's internal number
    notify_phone = os.getenv("NOTIFY_PHONE", "")
    if notify_phone:
        patient_name = state.get("patient_name") or state.get("user_name")
        number = phone.replace("@s.whatsapp.net", "")
        if patient_name:
            notification = f"👤 *{patient_name}* precisa de atendimento.\nNúmero: {number}"
        else:
            notification = f"👤 Um paciente precisa de atendimento.\nNúmero: {number}"
        await send_text(notify_phone, notification)

    await log_event("human_transfer", phone, {"reason": reason})
    await send_text(phone, "👤 Vou transferir você para um de nossos atendentes. Um momento, por favor!")
    return "Conversa transferida para atendente humano."
