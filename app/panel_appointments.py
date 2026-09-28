"""Alterar e cancelar consulta pelo painel da atendente.

Endpoints /admin/panel/appointments/edit e /cancel (app/main.py). Mesmas regras
da Eva: remarcação a pedido do paciente com menos de 24h não reaproveita a taxa
paga (reschedule_appointment), cancelamento com crédito vira pending_reschedule
(cancel_appointment com preserve_fee), devolução vai para a planilha de
Solicitações (register_refund_request). A mensagem ao paciente sai por
panel_booking.deliver_message: regra da idade, manual_hold, janela de 24h,
`messages` e checkpoint.
"""
import logging
from datetime import datetime, timedelta

from app.booking_texts import DOCTOR_LABELS, cancel_text, change_text, format_appt_line
from app.database import DOCTOR_IDS, get_supabase, log_event
from app.panel_booking import (
    SPLIT_PART1,
    SPLIT_PART2,
    BookingError,
    PanelInputError,
    _age_on,
    _is_minor_julio,
    _notify_clinic_async,
    _now,
    _parse_start,
    _thread,
    _to_local,
    check_slot,
    contact_first_name,
    create_appointments,
    deliver_message,
    preview_message,
)
from app.patients import _linked_contacts_with_marker, get_contact_by_phone, get_patient_by_id

_logger = logging.getLogger(__name__)

_DOCTOR_BY_ID = {v: k for k, v in DOCTOR_IDS.items()}
_ACTIVE = ("scheduled", "pending_reschedule")
_SLOT_FIELDS = {"start", "minutes", "doctor"}
_NOTIFY_FIELDS = {"start", "minutes", "doctor", "modality"}
BOOKING_FEE = "100,00"
_APPT_COLS = (
    "id, appointment_id, patient_id, contact_id, doctor_id, start_time, end_time, status, "
    "modality, consultation_type, session_note, booking_fee_paid_at, booking_fee_waived, "
    "is_courtesy, refund_requested_at, confirmed_at, paid_at"
)


# ── banco (funções pequenas para os testes trocarem) ────────────────────────

async def _fetch_row(appointment_id: str) -> dict | None:
    client = await get_supabase()
    res = await (client.from_("appointments").select(_APPT_COLS)
                 .eq("appointment_id", appointment_id).limit(1).execute())
    return (res.data or [None])[0]


async def _fetch_active_rows(patient_id: str) -> list[dict]:
    client = await get_supabase()
    res = await (client.from_("appointments").select(_APPT_COLS)
                 .eq("patient_id", patient_id).in_("status", list(_ACTIVE)).execute())
    return res.data or []


async def _update_row(row_id: str, fields: dict) -> None:
    """Atualiza pela coluna `id` (UUID da linha), nunca pelo id do Calendar."""
    client = await get_supabase()
    await client.from_("appointments").update(fields).eq("id", row_id).execute()


async def _delete_row(appointment_id: str) -> None:
    client = await get_supabase()
    await client.from_("appointments").delete().eq("appointment_id", appointment_id).execute()


# ── cobrança ───────────────────────────────────────────────────────────────

def current_billing(row: dict, patient: dict) -> str:
    if row.get("is_courtesy") or patient.get("custom_price") == 0:
        return "cortesia"
    if row.get("booking_fee_waived"):
        return "taxa_isenta"
    return "normal"


def fee_really_paid(row: dict, patient: dict) -> bool:
    """Taxa paga em dinheiro. Isenção e cortesia também gravam booking_fee_paid_at
    (data artificial), por isso a cobrança precisa ser a normal."""
    return bool(row.get("booking_fee_paid_at")) and current_billing(row, patient) == "normal"


def billing_update(row: dict, patient: dict, billing: str, now_iso: str) -> dict:
    """Campos de taxa para trocar a cobrança de uma consulta existente. {} se não mudou."""
    if patient.get("custom_price") == 0:
        billing = "cortesia"
    if billing == current_billing(row, patient):
        return {}
    if billing in ("cortesia", "taxa_isenta"):
        return {"is_courtesy": billing == "cortesia", "booking_fee_waived": True,
                "booking_fee_paid_at": row.get("booking_fee_paid_at") or now_iso}
    # Volta ao normal: a data de isenção/cortesia era artificial, a taxa volta a ser devida.
    # build_edit recusa essa volta; o lembrete zerado é só por segurança.
    return {"is_courtesy": False, "booking_fee_waived": False, "booking_fee_paid_at": None,
            "payment_reminder_sent_at": None}


# ── apoio ──────────────────────────────────────────────────────────────────

def _split_label(note: str | None) -> str:
    note = note or ""
    for label in (SPLIT_PART1, SPLIT_PART2):
        if note.startswith(label):
            return label
    return ""


def _display(patient: dict) -> str:
    name = patient.get("name") or ""
    return f"{name} ({patient['social_name']})" if patient.get("social_name") else name


def _dur(minutes: int) -> str:
    return {60: "1h", 120: "2h"}.get(minutes, f"{minutes}min")


def _line(doctor: str, start: datetime, note: str, modality: str, minutes: int) -> str:
    """Linha da mensagem de alteração: médico, data, observação, modalidade e duração,
    para a mudança aparecer mesmo quando só a modalidade ou a duração mudou."""
    mod = "Online" if modality == "online" else "Presencial"
    return f"{format_appt_line(doctor, start, note)} · {mod} · {_dur(minutes)}"


async def _load(body: dict) -> dict:
    """Consulta, paciente e contato da conversa, com as guardas comuns a alterar e cancelar."""
    phone = _thread(body.get("phone") or "")
    contact = await get_contact_by_phone(phone)
    if not contact:
        raise PanelInputError("contato não encontrado")
    row = await _fetch_row(body.get("appointment_id") or "")
    if not row or row.get("status") not in _ACTIVE or not row.get("patient_id"):
        raise PanelInputError("consulta não encontrada ou já encerrada")
    linked = await _linked_contacts_with_marker(row["patient_id"], include_inactive=True)
    if contact["id"] not in {lc["contact"]["id"] for lc in linked}:
        raise PanelInputError("esta consulta não pertence a este contato")
    patient = await get_patient_by_id(row["patient_id"])
    if not patient:
        raise PanelInputError("paciente não encontrado")
    initiated_by = body.get("initiated_by")
    if initiated_by not in (None, "patient", "clinic"):
        raise PanelInputError("quem pediu: paciente ou clínica")
    start, end = _to_local(row["start_time"]), _to_local(row["end_time"])
    return {
        "phone": phone, "contact": contact, "row": row, "patient": patient,
        "doctor": _DOCTOR_BY_ID.get(row["doctor_id"], ""), "start": start,
        "minutes": int((end - start).total_seconds() // 60),
        "initiated_by": initiated_by, "agent": (body.get("agent") or "").strip()[:80],
        # Quem recebe: a regra da idade parte do contato que agendou; sem ele, o da conversa.
        "recipient_contact_id": row.get("contact_id") or contact["id"],
    }


async def build_edit(body: dict) -> dict:
    """Valida a alteração e calcula o que mudou."""
    ctx = await _load(body)
    if ctx["initiated_by"] is None:
        raise PanelInputError("informe quem pediu a mudança: paciente ou clínica")
    row, patient = ctx["row"], ctx["patient"]

    doctor = body.get("doctor")
    if doctor not in DOCTOR_IDS:
        raise PanelInputError("médico inválido")
    modality = body.get("modality")
    if modality not in ("online", "presencial"):
        raise PanelInputError("modalidade inválida")
    restriction = patient.get("modality_restriction")
    if restriction in ("online", "presencial") and modality != restriction:
        raise PanelInputError(f"este paciente só pode ser atendido {restriction}")
    billing = body.get("billing", "normal")
    if billing not in ("normal", "taxa_isenta", "cortesia"):
        raise PanelInputError("cobrança inválida")
    try:
        minutes = int(body.get("minutes") or 0)
    except (TypeError, ValueError):
        raise PanelInputError("duração inválida")
    if minutes not in (40, 60, 120) or (minutes == 40 and doctor != "bruna"):
        raise PanelInputError("duração inválida")
    start = _parse_start(body.get("start"))
    # pending_reschedule não tem horário vigente: mesmo "sem mudar" o horário, ele não pode estar no passado.
    if (start != ctx["start"] or row["status"] == "pending_reschedule") and start < _now():
        raise PanelInputError("esse horário já passou")

    label = _split_label(row.get("session_note"))
    if label and (doctor != "julio" or minutes != 60):
        raise PanelInputError("parte da 1ª consulta dividida: 1h com o Dr. Júlio")
    extra = (body.get("session_note") or "").strip()[:80]
    note = " · ".join(x for x in (label, extra) if x)
    if label:
        ctype = "primeira_consulta"
    elif _is_minor_julio(patient, doctor, start.date()):
        ctype = "primeira_consulta" if body.get("first_consultation") else "acompanhamento"
    else:
        ctype = None
    if patient.get("custom_price") == 0:
        billing = "cortesia"

    compare = {
        "start": (start, ctx["start"]), "minutes": (minutes, ctx["minutes"]),
        "doctor": (doctor, ctx["doctor"]), "modality": (modality, row.get("modality") or modality),  # linha antiga sem modalidade
        "note": (note, row.get("session_note") or ""),
        "consultation_type": (ctype, row.get("consultation_type")),
        "billing": (billing, current_billing(row, patient)),
    }
    changes = {k for k, (new, old) in compare.items() if new != old}
    if not changes and row["status"] == "scheduled":
        raise PanelInputError("nada mudou")

    late_fee = (
        ctx["initiated_by"] == "patient" and "start" in changes and row["status"] == "scheduled"
        and fee_really_paid(row, patient) and _now() >= ctx["start"] - timedelta(hours=24)
        # Parte da 1ª consulta dividida: a taxa paga cobre as duas partes (decisão da clínica).
        and not _split_label(row.get("session_note"))
    )
    # Na remarcação tardia a taxa paga fica retida na linha antiga; a nova pode ser isenta.
    if (not late_fee and patient.get("custom_price") != 0
            and billing in ("taxa_isenta", "cortesia") and fee_really_paid(row, patient)):
        # Isentar por cima de taxa paga apagaria a data real do pagamento numa volta ao normal.
        raise PanelInputError("a taxa desta consulta já foi paga; para devolver, cancele com devolução")
    if not late_fee and billing == "normal" and current_billing(row, patient) != "normal":
        # O cron de cobrança conta o prazo por created_at/payment_reminder_sent_at e
        # cancelaria a consulta na hora. Na remarcação tardia a linha é nova: pode.
        raise PanelInputError("para voltar a cobrar a taxa, cancele esta consulta e agende de novo")
    return {
        **ctx,
        "new": {"doctor": doctor, "modality": modality, "start": start, "minutes": minutes,
                "note": note, "ctype": ctype, "billing": billing},
        "changes": changes,
        # pending_reschedule sem evento no Calendar: remarcar sempre avisa o paciente.
        "notify": bool(changes & _NOTIFY_FIELDS) or row["status"] == "pending_reschedule",
        "late_fee": late_fee,
    }


def _event_kwargs(req: dict, doctor: str, start: datetime, minutes: int, note: str, modality: str,
                  ctype: str | None) -> dict:
    patient = req["patient"]
    return {
        "slot_minutes": minutes, "patient_name": _display(patient), "doctor_name": DOCTOR_LABELS[doctor],
        "session_note": note, "modality": modality, "patient_email": patient.get("email") or "",
        "patient_number": req["phone"],
        # 1ª consulta de menor em 2h sem observação: descrição "1ª hora pais / 2ª hora paciente".
        "is_minor_first": ctype == "primeira_consulta" and minutes == 120,
    }


async def apply_edit(req: dict) -> dict:
    """Altera a mesma linha. Calendar primeiro; se o banco falhar, o Calendar volta."""
    from app.google_calendar import cancel_event, create_event, update_event
    from app.graph.tools import _get_doctor_calendar_id

    row, new, patient = req["row"], req["new"], req["patient"]
    old_doctor, new_doctor = req["doctor"], new["doctor"]
    now_iso = _now().isoformat()
    try:
        new_cal = await _get_doctor_calendar_id(new_doctor)
    except Exception as exc:
        raise BookingError("calendário do médico não encontrado; nada foi alterado") from exc
    if not new_cal:
        raise BookingError("calendário do médico não encontrado; nada foi alterado")

    fields = {
        "start_time": new["start"].isoformat(),
        "end_time": (new["start"] + timedelta(minutes=new["minutes"])).isoformat(),
        "doctor_id": DOCTOR_IDS[new_doctor], "modality": new["modality"],
        "session_note": new["note"] or None, "consultation_type": new["ctype"],
        "updated_at": now_iso, **billing_update(row, patient, new["billing"], now_iso),
    }
    pending = row["status"] == "pending_reschedule"
    reset_confirmation = pending or bool(req["changes"] & {"start", "doctor"})
    if reset_confirmation:
        # Confirmação e lembretes valem para a data antiga (ver caso Isaac, reschedule_appointment).
        fields.update({"confirmed_at": None, "reminder_day_before_sent_at": None,
                       "reminder_day_of_sent_at": None, "reschedule_initiated_by": req["initiated_by"]})
    if pending:
        fields.update({"status": "scheduled", "reschedule_requested_at": None})

    ev = _event_kwargs(req, new_doctor, new["start"], new["minutes"], new["note"], new["modality"], new["ctype"])
    created = None
    try:
        if pending or new_doctor != old_doctor:
            created = await create_event(calendar_id=new_cal, start=new["start"], **ev)
            fields["appointment_id"] = created
        else:
            # O patch reescreve o título: sem isto o ✅ verde da confirmação sumiria.
            await update_event(calendar_id=new_cal, event_id=row["appointment_id"], new_start=new["start"],
                               confirmed=bool(row.get("confirmed_at")) and not reset_confirmation, **ev)
    except Exception as exc:
        _logger.exception("panel edit: Calendar falhou appt=%s", row["appointment_id"])
        raise BookingError("não foi possível atualizar a agenda; nada foi alterado") from exc

    try:
        await _update_row(row["id"], fields)
    except Exception as exc:
        _logger.exception("panel edit: banco falhou appt=%s", row["appointment_id"])
        try:
            if created:
                await cancel_event(new_cal, created)
            else:
                old = _event_kwargs(req, old_doctor, req["start"], req["minutes"],
                                    row.get("session_note") or "", row.get("modality") or "",
                                    row.get("consultation_type"))
                await update_event(calendar_id=new_cal, event_id=row["appointment_id"], new_start=req["start"],
                                   confirmed=bool(row.get("confirmed_at")), **old)
        except Exception:
            _logger.exception("panel edit: falha ao desfazer o Calendar appt=%s", row["appointment_id"])
        raise BookingError("não foi possível gravar a alteração; nada foi alterado") from exc

    warnings = []
    if created and not pending:
        # Médico trocado: o evento antigo sai do calendário do médico anterior.
        try:
            await cancel_event(await _get_doctor_calendar_id(old_doctor), row["appointment_id"])
        except Exception:
            _logger.exception("panel edit: evento antigo ficou no Calendar appt=%s", row["appointment_id"])
            warnings.append("o horário antigo continua na agenda do médico anterior; apague à mão")

    appointment_id = fields.get("appointment_id", row["appointment_id"])
    meta = {"origem": "painel", "atendente": req["agent"], "encaixe": bool(req.get("encaixe")),
            "initiated_by": req["initiated_by"]}
    await log_event("appointment_edited", req["phone"], {
        "appointment_id": appointment_id, "changes": sorted(req["changes"]), **meta})
    if pending or "start" in req["changes"]:
        # Mesmo evento que reschedule_appointment grava: a política de 1 remarcação conta por ele.
        await log_event("appointment_rescheduled", req["phone"], {
            "appointment_id": appointment_id, "new_datetime": new["start"].replace(tzinfo=None).isoformat(),
            "fee_paid": bool(row.get("booking_fee_paid_at") or row.get("booking_fee_waived")), **meta})

    old_line = _line(old_doctor, req["start"], row.get("session_note") or "", row.get("modality") or "", req["minutes"])
    new_line = _line(new_doctor, new["start"], new["note"], new["modality"], new["minutes"])
    if req["notify"]:
        who = "Clínica" if req["initiated_by"] == "clinic" else "Paciente"
        _notify_clinic_async(
            f"Agendamento alterado — {_display(patient)}",
            "Agendamento alterado pelo painel 🔄\n"
            f"Paciente: {_display(patient)}\nHorário anterior: {old_line}\nNovo horário: {new_line}\n"
            f"Quem pediu: {who}\nAtendente: {req['agent'] or '—'}"
            + ("\n⚠️ Encaixe fora da grade" if req.get("encaixe") else ""),
            req["phone"],
        )
    return {"appointment_id": appointment_id, "old_line": old_line, "new_line": new_line,
            "new_fee": False, "warnings": warnings}


async def apply_late_reschedule(req: dict) -> dict:
    """Paciente pediu para remarcar com menos de 24h e a taxa foi paga: a taxa fica
    retida na linha antiga (canceled) e nasce uma linha nova com a cobrança escolhida
    (normal = nova taxa). Mesmo resultado de cancel_appointment + confirm_appointment
    que reschedule_appointment manda a Eva fazer nesse caso."""
    from app.google_calendar import cancel_event
    from app.graph.tools import _get_doctor_calendar_id

    row, new, patient = req["row"], req["new"], req["patient"]
    booking = {
        "phone": req["phone"], "contact_id": req["recipient_contact_id"], "patient": patient,
        "doctor": new["doctor"], "modality": new["modality"],
        "parts": [{"start": new["start"], "minutes": new["minutes"]}],
        "split": False, "split_of": None, "session_note": new["note"],
        "first_consultation": new["ctype"] == "primeira_consulta", "billing": new["billing"],
        "encaixe": bool(req.get("encaixe")), "agent": req["agent"],
        "notify_clinic": False,  # a clínica recebe só o aviso da remarcação <24h
    }
    created = await create_appointments(booking)  # tudo ou nada; BookingError sobe
    new_id = created["appointments"][0]["appointment_id"]
    now_iso = _now().isoformat()
    try:
        await _update_row(row["id"], {"status": "canceled", "updated_at": now_iso})
    except Exception as exc:
        _logger.exception("panel late reschedule: falha ao cancelar a linha antiga appt=%s", row["appointment_id"])
        try:
            await cancel_event(await _get_doctor_calendar_id(new["doctor"]), new_id)
            await _delete_row(new_id)
        except Exception:
            _logger.exception("panel late reschedule: falha ao desfazer a linha nova %s", new_id)
        raise BookingError("não foi possível concluir a remarcação; confira a lista de consultas") from exc

    old_line = _line(req["doctor"], req["start"], row.get("session_note") or "", row.get("modality") or "", req["minutes"])
    new_line = _line(new["doctor"], new["start"], new["note"], new["modality"], new["minutes"])
    new_fee = created["kind"] == "normal"
    _notify_clinic_async(
        f"Agendamento alterado — {_display(patient)}",
        "Remarcação a pedido do paciente com menos de 24h 🔄\n"
        f"Paciente: {_display(patient)}\nHorário anterior: {old_line}\nNovo horário: {new_line}\n"
        "A taxa anterior fica retida" + ("; nova taxa de reserva cobrada" if new_fee else "")
        + f"\nAtendente: {req['agent'] or '—'}",
        req["phone"],
    )

    warnings = []
    try:
        await cancel_event(await _get_doctor_calendar_id(req["doctor"]), row["appointment_id"])
    except Exception:
        _logger.exception("panel late reschedule: evento antigo ficou no Calendar appt=%s", row["appointment_id"])
        warnings.append("o horário antigo continua na agenda; apague à mão")
    await log_event("appointment_canceled", req["phone"], {
        "appointment_id": row["appointment_id"], "preserve_fee": False, "origem": "painel",
        "atendente": req["agent"], "initiated_by": "patient", "fee_action": "reter",
        "reason": "remarcação com menos de 24h a pedido do paciente", "replaced_by": new_id,
    })
    return {
        "appointment_id": new_id,
        "old_line": old_line,
        "new_line": new_line,
        "new_fee": new_fee,
        "warnings": warnings,
    }


_NO_MESSAGE = {"sent": [], "not_delivered": [], "held": [], "skipped": True}


async def handle_edit(body: dict) -> tuple[int, dict]:
    """Fluxo de POST /admin/panel/appointments/edit. Devolve (status_http, corpo)."""
    req = await build_edit(body)
    new, row = req["new"], req["row"]
    reasons: list[str] = []
    moves = bool(req["changes"] & _SLOT_FIELDS) or row["status"] == "pending_reschedule"
    if moves and not body.get("encaixe_confirmed"):
        reasons = await check_slot(new["doctor"], new["start"], new["minutes"], req["patient"]["id"],
                                   exclude_appointment_id=row["appointment_id"])

    def text_for(lines: tuple[str, str], new_fee: bool):
        return lambda c: change_text(req["initiated_by"], contact_first_name(c), lines[0], lines[1], new_fee)

    if body.get("dry_run"):
        message = None
        if req["notify"]:
            old_line = _line(req["doctor"], req["start"], row.get("session_note") or "",
                             row.get("modality") or "", req["minutes"])
            new_line = _line(new["doctor"], new["start"], new["note"], new["modality"], new["minutes"])
            new_fee = req["late_fee"] and new["billing"] == "normal"
            message = await preview_message(req["patient"]["id"], req["recipient_contact_id"],
                                            text_for((old_line, new_line), new_fee))
        return 200, {"encaixe_reasons": reasons, "late_fee": req["late_fee"],
                     "notify": req["notify"], "message": message}

    if reasons:
        return 409, {"detail": {"needs_encaixe": True, "reasons": reasons}}

    req["encaixe"] = bool(body.get("encaixe_confirmed"))
    result = await (apply_late_reschedule if req["late_fee"] else apply_edit)(req)
    if req["notify"]:
        msg = await deliver_message(req["patient"]["id"], req["recipient_contact_id"],
                                    text_for((result["old_line"], result["new_line"]), result["new_fee"]),
                                    _display(req["patient"]), new["doctor"])
    else:
        msg = dict(_NO_MESSAGE)
    return 200, {"appointment_id": result["appointment_id"], "warnings": result["warnings"], "message": msg}


_FEE_ACTIONS = ("devolver", "credito", "reter")


async def _split_sibling(row: dict) -> dict | None:
    """A outra parte ativa da 1ª consulta dividida, se houver."""
    label = _split_label(row.get("session_note"))
    if not label:
        return None
    other = SPLIT_PART2 if label == SPLIT_PART1 else SPLIT_PART1
    for r in await _fetch_active_rows(row["patient_id"]):
        if r["appointment_id"] != row["appointment_id"] and (r.get("session_note") or "").startswith(other):
            return r
    return None


async def build_cancel(body: dict) -> dict:
    """Valida o cancelamento. Em dry_run só lê: quem pediu e a taxa podem faltar."""
    ctx = await _load(body)
    row, patient = ctx["row"], ctx["patient"]
    paid = fee_really_paid(row, patient)
    hours = (ctx["start"] - _now()).total_seconds() / 3600
    sibling = await _split_sibling(row)
    fee_action = body.get("fee_action") if paid else None
    reason = (body.get("reason") or "").strip()[:200]
    both_parts = bool(body.get("both_parts")) and sibling is not None
    # pending_reschedule já saiu da agenda: a regra das 24h não se aplica a ela.
    late = row["status"] == "scheduled" and hours < 24
    if row["status"] == "scheduled" and hours < 0:
        raise PanelInputError("essa consulta já passou; marque como realizada ou falta na aba Financeiro")
    if not body.get("dry_run"):
        if ctx["initiated_by"] is None:
            raise PanelInputError("informe quem pediu o cancelamento: paciente ou clínica")
        if paid and fee_action not in _FEE_ACTIONS:
            raise PanelInputError("escolha o que fazer com a taxa paga")
        if fee_action == "credito" and row["status"] == "pending_reschedule":
            raise PanelInputError("a taxa desta consulta já está guardada como crédito")
        if fee_action == "devolver" and row.get("refund_requested_at"):
            raise PanelInputError("a devolução desta taxa já foi pedida")
        if fee_action == "devolver" and sibling is not None and not both_parts:
            raise PanelInputError("a taxa cobre as duas partes da 1ª consulta; para devolver, cancele as duas")
        if fee_action == "devolver" and late and not reason:
            raise PanelInputError("com menos de 24h, informe o motivo da devolução")
    return {**ctx, "fee_paid": paid, "hours_until": hours, "policy_late": late,
            "sibling": sibling, "fee_action": fee_action, "reason": reason, "both_parts": both_parts}


def _already_gone(exc: Exception) -> bool:
    """Evento já apagado à mão no Calendar (404/410): o cancelamento segue."""
    return getattr(getattr(exc, "resp", None), "status", None) in (404, 410)


async def _cancel_row(row: dict, new_status: str) -> None:
    """Banco primeiro, depois Calendar. Se o Calendar falhar, o status volta."""
    from app.google_calendar import cancel_event
    from app.graph.tools import _get_doctor_calendar_id

    now_iso = _now().isoformat()
    try:
        await _update_row(row["id"], {"status": new_status, "updated_at": now_iso})
    except Exception as exc:
        _logger.exception("panel cancel: banco falhou appt=%s", row["appointment_id"])
        raise BookingError("não foi possível gravar o cancelamento; nada foi alterado") from exc
    if row["status"] != "scheduled":
        return  # pending_reschedule: o evento já saiu do Calendar
    try:
        cal = await _get_doctor_calendar_id(_DOCTOR_BY_ID.get(row["doctor_id"], ""))
        await cancel_event(cal, row["appointment_id"])
    except Exception as exc:
        if _already_gone(exc):
            return
        _logger.exception("panel cancel: Calendar falhou appt=%s", row["appointment_id"])
        try:
            await _update_row(row["id"], {"status": row["status"], "updated_at": now_iso})
        except Exception:
            _logger.exception("panel cancel: falha ao restaurar status appt=%s", row["appointment_id"])
        raise BookingError("não foi possível tirar a consulta da agenda; nada foi alterado") from exc


async def _register_refund(req: dict) -> list[str]:
    """Mesma gravação de register_refund_request: refund_requested_at e linha
    "Solicitação de Reembolso" na planilha de Solicitações. A baixa depois segue
    confirm_refund_completed. Falhas aqui viram aviso: a consulta já foi cancelada."""
    from app.google_sheets import append_document_request

    row, patient = req["row"], req["patient"]
    warnings = []
    now_iso = _now().isoformat()
    try:
        await _update_row(row["id"], {"refund_requested_at": now_iso, "updated_at": now_iso})
    except Exception:
        _logger.exception("panel cancel: refund_requested_at falhou appt=%s", row["appointment_id"])
        warnings.append("o pedido de devolução não foi marcado na consulta; avise a equipe")
    when = req["start"].strftime("%d/%m/%Y às %H:%M")
    reason = req["reason"] or "cancelamento pelo painel"
    try:
        await append_document_request(
            patient_name=_display(patient),
            patient_age=_age_on(patient.get("birth_date"), _now().date()),
            phone=req["phone"],
            patient_email=patient.get("email") or "",
            document_type="Solicitação de Reembolso",
            medication_note=f"Valor: R$ {BOOKING_FEE} | Consulta: {when} | Motivo: {reason}",
            doctor_name=DOCTOR_LABELS.get(req["doctor"], "médico(a)"),
            patient_cpf=patient.get("patient_cpf") or "",
        )
    except Exception:
        _logger.exception("panel cancel: planilha de Solicitações falhou appt=%s", row["appointment_id"])
        warnings.append("a devolução não entrou na planilha de Solicitações; registre à mão")
    if row.get("paid_at"):
        warnings.append("a consulta já estava paga por inteiro; a devolução registrada é só a taxa "
                        f"de R$ {BOOKING_FEE} — confira o valor com a equipe")
    await log_event("refund_requested", req["phone"], {
        "appointment_id": row["appointment_id"], "amount": BOOKING_FEE, "reason": reason,
        "origem": "painel", "atendente": req["agent"]})
    return warnings


def _row_line(row: dict) -> str:
    start = _to_local(row["start_time"])
    return format_appt_line(_DOCTOR_BY_ID.get(row["doctor_id"], ""), start, row.get("session_note") or "")


async def apply_cancel(req: dict) -> dict:
    rows = [req["row"]] + ([req["sibling"]] if req["both_parts"] else [])
    new_status = "pending_reschedule" if req["fee_action"] == "credito" else "canceled"
    done: list[dict] = []
    warnings: list[str] = []
    for r in rows:
        try:
            await _cancel_row(r, new_status)
        except BookingError:
            if not done:
                raise
            # A 1ª já foi cancelada: segue só com ela e avisa da outra parte.
            warnings.append("a outra parte da 1ª consulta não foi cancelada; cancele à mão")
            continue
        done.append(r)
        await log_event("appointment_canceled", req["phone"], {
            "appointment_id": r["appointment_id"], "preserve_fee": new_status == "pending_reschedule",
            "origem": "painel", "atendente": req["agent"], "initiated_by": req["initiated_by"],
            "fee_action": req["fee_action"], "reason": req["reason"]})

    if req["fee_action"] == "devolver":
        if len(done) < len(rows):
            # A taxa cobre as duas partes: sem a outra cancelada, não se devolve.
            warnings.append("a devolução não foi registrada porque a outra parte continua marcada; "
                            "cancele a outra parte e peça a devolução")
        else:
            warnings += await _register_refund(req)

    lines = [_row_line(r) for r in done]
    who = "Clínica" if req["initiated_by"] == "clinic" else "Paciente"
    fee_note = {"devolver": "Taxa: devolver ao paciente (ver planilha de Solicitações)",
                "credito": "Taxa: guardada para remarcar", "reter": "Taxa: retida"}.get(req["fee_action"] or "", "")
    if req["fee_action"] == "devolver" and len(done) < len(rows):
        fee_note = "Taxa: devolução NÃO registrada (a outra parte continua marcada)"
    title = "Consulta liberada para remarcação 🔄" if new_status == "pending_reschedule" else "Agendamento cancelado pelo painel ❌"
    _notify_clinic_async(
        f"{'Consulta liberada para remarcação' if new_status == 'pending_reschedule' else 'Agendamento cancelado'} — {_display(req['patient'])}",
        f"{title}\nPaciente: {_display(req['patient'])}\n" + "\n".join(lines)
        + f"\nQuem pediu: {who}" + (f"\n{fee_note}" if fee_note else "")
        + (f"\nMotivo: {req['reason']}" if req["reason"] else "") + f"\nAtendente: {req['agent'] or '—'}",
        req["phone"],
    )
    return {"canceled": [r["appointment_id"] for r in done], "lines": lines, "warnings": warnings}


async def handle_cancel(body: dict) -> tuple[int, dict]:
    """Fluxo de POST /admin/panel/appointments/cancel. Devolve (status_http, corpo)."""
    req = await build_cancel(body)

    def text_for(lines: list[str]):
        return lambda c: cancel_text(req["initiated_by"], contact_first_name(c), lines, req["fee_action"])

    if body.get("dry_run"):
        sib = req["sibling"]
        lines = [_row_line(req["row"])] + ([_row_line(sib)] if req["both_parts"] else [])
        message = None
        if req["initiated_by"]:
            message = await preview_message(req["patient"]["id"], req["recipient_contact_id"], text_for(lines))
        return 200, {
            "fee_paid": req["fee_paid"], "status": req["row"]["status"],
            "hours_until": round(req["hours_until"], 1) if req["row"]["status"] == "scheduled" else None,
            "policy_late": req["policy_late"],
            "sibling": {"appointment_id": sib["appointment_id"], "line": _row_line(sib)} if sib else None,
            "message": message,
        }

    result = await apply_cancel(req)
    msg = await deliver_message(req["patient"]["id"], req["recipient_contact_id"], text_for(result["lines"]),
                                _display(req["patient"]), req["doctor"])
    return 200, {"canceled": result["canceled"], "warnings": result["warnings"], "message": msg}
