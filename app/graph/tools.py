from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState

from app.uazapi import send_text
from app.database import get_supabase, DOCTOR_IDS

TZ = ZoneInfo("America/Recife")


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

    calendar_id = await _get_doctor_calendar_id(state.get("preferred_doctor", ""))
    if not calendar_id:
        return "Não foi possível identificar o calendário do médico."

    slots = await _get_slots(
        calendar_id=calendar_id,
        preferred_day=preferred_day,
        preferred_shift=preferred_shift,
        slot_minutes=slot_duration_minutes,
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
) -> str:
    """
    Confirma e cria o agendamento no Google Calendar.
    slot_datetime deve estar no formato ISO 8601, ex: '2026-03-19T09:00:00'.
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
    is_minor_first = patient_age < 18 and not state.get("is_patient", False)

    event_id = await create_event(
        calendar_id=calendar_id,
        start=start,
        slot_minutes=slot_duration_minutes,
        patient_name=patient_name,
        doctor_name=doctor_label,
        is_minor_first=is_minor_first,
    )

    formatted = start.strftime("%d/%m/%Y às %H:%M")
    return f"Consulta agendada com sucesso! ✅\n{doctor_label} — {formatted}\nID: {event_id}"


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

    return f"Solicitação de {document_type} registrada com sucesso. Em breve entraremos em contato."


@tool
async def transfer_to_human(
    reason: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Transfere a conversa para um atendente humano quando o bot não consegue ajudar."""
    phone = config["configurable"]["phone"]
    await send_text(phone, "👤 Vou transferir você para um de nossos atendentes. Um momento, por favor!")
    return "Conversa transferida para atendente humano."
