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
