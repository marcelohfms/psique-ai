import os
from datetime import datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState

from app.uazapi import send_text
from app.database import get_supabase, log_event, upsert_user, get_user_by_phone, DOCTOR_IDS, DOCTOR_NAMES

TZ = ZoneInfo("America/Recife")

APPOINTMENT_NOTIFY_PHONE = os.getenv("APPOINTMENT_NOTIFY_PHONE", "5583998566516")


async def _notify_clinic(message: str) -> None:
    """Envia notificação de agendamento para a atendente da clínica."""
    if APPOINTMENT_NOTIFY_PHONE:
        try:
            await send_text(APPOINTMENT_NOTIFY_PHONE, message)
        except Exception:
            pass  # Não interrompe o fluxo se a notificação falhar


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

    _logger.info("CONFIRM_DEBUG2 patient=%s calendar=%s start=%s", patient_name, calendar_id, start)

    try:
        event_id = await create_event(
            calendar_id=calendar_id,
            start=start,
            slot_minutes=slot_duration_minutes,
            patient_name=patient_name,
            doctor_name=doctor_label,
            is_minor_first=is_minor_first,
            session_note=session_note,
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
    document_type: Literal["nota_fiscal", "laudo", "exame", "relatorio", "receita", "declaracao"],
    patient_email: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Registra uma solicitação de documento médico para o paciente.
    patient_email: e-mail informado pelo paciente para recebimento do documento.
    """
    import logging as _log
    _log.getLogger(__name__).warning("REQUEST_DOC_CALLED type=%s email=%s", document_type, patient_email)

    from app.google_sheets import append_document_request
    from app.email_sender import send_document_request_email

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
        await append_document_request(patient_name, patient_age, phone, patient_email, document_type)
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
    await _notify_clinic(
        f"📄 Solicitação de {doc_label}\n"
        f"Paciente: {patient_name}\n"
        f"Médico(a): {doctor_label}\n"
        f"E-mail: {patient_email}\n"
        f"WhatsApp: {phone_clean}"
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
) -> str:
    """
    Registra um comprovante de pagamento PIX na planilha.
    Chame quando o paciente enviar imagem de comprovante — ela aparecerá no histórico como
    "[imagem]: descrição... [drive_link:URL]".
    amount: valor pago extraído da descrição (ex: "100,00"). Use "?" se não identificado.
    drive_link: URL extraída da tag [drive_link:URL] na descrição. Passe "" se não houver.
    """
    from app.google_sheets import append_payment_receipt

    phone = config["configurable"]["phone"]
    patient_name = state.get("patient_name") or state.get("user_name", "Paciente")
    doctor_key = state.get("preferred_doctor", "")
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(doctor_key, "médico(a)")

    # Fetch next scheduled appointment date
    client = await get_supabase()
    user = await get_user_by_phone(phone)
    appointment_dt = "—"
    if user:
        result = await client.from_("appointments").select("start_time").eq("user_id", user["id"]).eq("status", "scheduled").order("start_time").limit(1).execute()
        if result.data:
            apt_start = datetime.fromisoformat(result.data[0]["start_time"]).astimezone(TZ)
            appointment_dt = apt_start.strftime("%d/%m/%Y %H:%M")

    # Rename Drive file with patient name + appointment date + amount
    if drive_link:
        try:
            from app.google_drive import rename_file
            file_id = drive_link.split("/d/")[1].split("/")[0]
            amount_clean = amount.replace("R$", "").replace(" ", "").strip()
            date_clean = appointment_dt.split(" ")[0].replace("/", "-") if appointment_dt != "—" else datetime.now(TZ).strftime("%d-%m-%Y")
            safe_name = patient_name.replace(" ", "_")
            new_filename = f"{safe_name}_{date_clean}_R${amount_clean}.jpg"
            await rename_file(file_id, new_filename)
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("DRIVE_RENAME FAILED")

    try:
        await append_payment_receipt(patient_name, phone, doctor_label, appointment_dt, amount, drive_link)
    except Exception:
        import logging as _log
        _log.getLogger(__name__).exception("SHEETS_APPEND FAILED patient=%s", patient_name)

    # Mark appointment as paid so the payment reminder script skips it
    if user:
        try:
            await client.from_("appointments").update({
                "paid_at": datetime.now(TZ).isoformat(),
            }).eq("user_id", user["id"]).eq("status", "scheduled").order("start_time").limit(1).execute()
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("PAID_AT UPDATE FAILED patient=%s", patient_name)

    await _notify_clinic(
        f"💰 Comprovante recebido!\nPaciente: {patient_name}\nValor: R$ {amount}\nConsulta: {appointment_dt}\nLink: {drive_link}"
    )

    await log_event("payment_receipt_registered", phone, {
        "patient_name": patient_name,
        "amount": amount,
        "drive_link": drive_link,
    })

    return "Comprovante recebido e registrado com sucesso! ✅ Sua vaga está garantida."


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
    from datetime import datetime, timezone
    await upsert_user(phone, {"active": False, "deactivated_at": datetime.now(timezone.utc).isoformat()})

    # Notify the clinic's internal number
    notify_phone = os.getenv("NOTIFY_PHONE", "")
    if notify_phone:
        patient_name = state.get("patient_name") or state.get("user_name")
        number = phone.replace("@s.whatsapp.net", "")
        if patient_name:
            notification = f"👤 *{patient_name}* precisa de atendimento.\nNúmero: {number}"
        else:
            notification = f"👤 Um paciente precisa de atendimento.\nNúmero: {number}"
        if reason:
            notification += f"\nMotivo: {reason}"
        await send_text(notify_phone, notification)

    await log_event("human_transfer", phone, {"reason": reason})
    await send_text(phone, "👤 Vou transferir você para um de nossos atendentes. Um momento, por favor!")
    return "Conversa transferida para atendente humano."
