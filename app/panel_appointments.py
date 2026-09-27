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
    "is_courtesy, refund_requested_at"
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
    return {"is_courtesy": False, "booking_fee_waived": False, "booking_fee_paid_at": None}


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
    if start != ctx["start"] and start < _now():
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
        "doctor": (doctor, ctx["doctor"]), "modality": (modality, row.get("modality")),
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
    )
    return {
        **ctx,
        "new": {"doctor": doctor, "modality": modality, "start": start, "minutes": minutes,
                "note": note, "ctype": ctype, "billing": billing},
        "changes": changes,
        # pending_reschedule sem evento no Calendar: remarcar sempre avisa o paciente.
        "notify": bool(changes & _NOTIFY_FIELDS) or row["status"] == "pending_reschedule",
        "late_fee": late_fee,
    }


def _event_kwargs(req: dict, doctor: str, start: datetime, minutes: int, note: str, modality: str) -> dict:
    patient = req["patient"]
    return {
        "slot_minutes": minutes, "patient_name": _display(patient), "doctor_name": DOCTOR_LABELS[doctor],
        "session_note": note, "modality": modality, "patient_email": patient.get("email") or "",
        "patient_number": req["phone"],
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
    if pending or req["changes"] & {"start", "doctor"}:
        # Confirmação e lembretes valem para a data antiga (ver caso Isaac, reschedule_appointment).
        fields.update({"confirmed_at": None, "reminder_day_before_sent_at": None,
                       "reminder_day_of_sent_at": None, "reschedule_initiated_by": req["initiated_by"]})
    if pending:
        fields.update({"status": "scheduled", "reschedule_requested_at": None})

    ev = _event_kwargs(req, new_doctor, new["start"], new["minutes"], new["note"], new["modality"])
    created = None
    try:
        if pending or new_doctor != old_doctor:
            created = await create_event(calendar_id=new_cal, start=new["start"], **ev)
            fields["appointment_id"] = created
        else:
            await update_event(calendar_id=new_cal, event_id=row["appointment_id"], new_start=new["start"], **ev)
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
                                    row.get("session_note") or "", row.get("modality") or "")
                await update_event(calendar_id=new_cal, event_id=row["appointment_id"], new_start=req["start"], **old)
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
    if pending or req["changes"] & {"start", "doctor"}:
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
