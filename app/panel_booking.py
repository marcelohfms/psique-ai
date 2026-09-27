"""Agendamento feito pela atendente no painel (endpoint /admin/panel/appointments).

Reaproveita as regras da Eva: grade (grid_violation), textos (booking_texts),
destinatários pela regra da idade (consultation_reminder_contacts). A mensagem
enviada entra em `messages` e no checkpoint da conversa, para a Eva saber do que
se trata quando o paciente responder.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.booking_texts import DOCTOR_LABELS, format_appt_line
from app.database import DOCTOR_IDS, get_supabase, log_event
from app.google_calendar import grid_violation
from app.utils import display_name


class BookingError(Exception):
    """Falha ao gravar; nada ficou criado (eventos do Calendar já apagados)."""

_logger = logging.getLogger(__name__)
TZ = ZoneInfo("America/Recife")

# Mesmos textos em dashboard/templates/atendente.html (SPLIT_PART1/SPLIT_PART2).
SPLIT_PART1 = "1ª consulta · parte 1 de 2"
SPLIT_PART2 = "1ª consulta · parte 2 de 2"

_GRID_REASONS = {
    "dia_bloqueado": "dia bloqueado na agenda",
    "fora_da_excecao": "fora do horário de atendimento",
    "dia_sem_atendimento": "o médico não atende neste dia da semana",
    "fora_da_grade": "fora do horário de atendimento",
    "estoura_expediente": "passa do fim do expediente",
}


def _to_local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


async def _calendar_busy(doctor: str, start: datetime, end: datetime) -> list[dict]:
    """Eventos da Eva no Calendar do médico entre start e end. Erro = lista vazia
    (a checagem no banco continua valendo)."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import _credentials, _get_busy
    from googleapiclient.discovery import build
    try:
        calendar_id = await _get_doctor_calendar_id(doctor)
        service = build("calendar", "v3", credentials=_credentials())
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _get_busy, service, calendar_id, start, end)
    except Exception:
        _logger.exception("panel check_slot: falha ao ler o Calendar doctor=%s", doctor)
        return []


async def check_slot(doctor: str, start: datetime, minutes: int, patient_id: str) -> list[str]:
    """Motivos pelos quais o horário é encaixe. Lista vazia = horário livre na grade.

    Consultas do próprio paciente não contam como conflito (a 2ª parte da 1ª
    consulta infantil pode encostar na 1ª)."""
    reasons: list[str] = []
    code = grid_violation(doctor, start, minutes)
    if code:
        reasons.append(_GRID_REASONS[code])

    end = start + timedelta(minutes=minutes)
    client = await get_supabase()
    res = await (
        client.from_("appointments")
        .select("patient_id, start_time, patients(name)")
        .eq("doctor_id", DOCTOR_IDS[doctor])
        .eq("status", "scheduled")
        .lt("start_time", end.isoformat())
        .gt("end_time", start.isoformat())
        .execute()
    )
    clash_starts = set()
    for row in res.data or []:
        if row.get("patient_id") == patient_id:
            continue
        other = display_name((row.get("patients") or {}).get("name") or "") or "outro paciente"
        hhmm = _to_local(row["start_time"]).strftime("%H:%M")
        clash_starts.add(hhmm)
        reasons.append(f"bate com a consulta de {other} às {hhmm}")

    # Calendar pega também o que foi marcado à mão pela clínica (evento "Consulta ...").
    for ev in await _calendar_busy(doctor, start, end):
        hhmm = _to_local(ev["start"]).strftime("%H:%M")
        if hhmm not in clash_starts:
            reasons.append(f"agenda do médico ocupada às {hhmm}")
            clash_starts.add(hhmm)
    return reasons


def _notify_clinic_async(subject: str, body: str, phone: str) -> None:
    from app.graph.tools import _notify_clinic
    asyncio.create_task(_notify_clinic(body, phone=phone, subject=subject))


def _fee_fields(req: dict, now_iso: str) -> tuple[dict, str]:
    """Campos de taxa da linha e o tipo de mensagem (booking_texts.confirmation_text)."""
    part1 = req.get("split_of")
    if part1:
        # 2ª parte herda a situação da 1ª, como confirm_appointment faz.
        fields = {
            "booking_fee_waived": bool(part1.get("booking_fee_waived")),
            "booking_fee_paid_at": part1.get("booking_fee_paid_at"),
            "is_courtesy": bool(part1.get("is_courtesy")),
        }
        if fields["is_courtesy"]:
            return fields, "cortesia"
        if fields["booking_fee_waived"]:
            return fields, "taxa_isenta"
        if fields["booking_fee_paid_at"]:
            return fields, "taxa_paga"
        return fields, "normal"
    billing = req["billing"]
    if billing == "cortesia":
        # booking_fee_waived também, para todo filtro antigo por taxa já pular a cortesia.
        return {"booking_fee_waived": True, "booking_fee_paid_at": now_iso, "is_courtesy": True}, "cortesia"
    if billing == "taxa_isenta":
        return {"booking_fee_waived": True, "booking_fee_paid_at": now_iso, "is_courtesy": False}, "taxa_isenta"
    return {"booking_fee_waived": False, "booking_fee_paid_at": None, "is_courtesy": False}, "normal"


def _part_notes(req: dict) -> list[str]:
    extra = (req.get("session_note") or "").strip()
    if req.get("split_of"):
        labels = [SPLIT_PART2]
    elif req.get("split"):
        labels = [SPLIT_PART1, SPLIT_PART2][: len(req["parts"])]
    else:
        labels = [""]
    return [" · ".join(x for x in (label, extra) if x) for label in labels]


def _is_minor_julio(patient: dict, doctor: str) -> bool:
    from app.patients import _compute_age
    age = _compute_age(patient.get("birth_date"))
    return doctor == "julio" and age is not None and age < 18


async def create_appointments(req: dict) -> dict:
    """Cria o(s) evento(s) e a(s) linha(s). Tudo ou nada."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event, create_event

    doctor = req["doctor"]
    patient = req["patient"]
    name = patient.get("name") or ""
    display = f"{name} ({patient['social_name']})" if patient.get("social_name") else name
    calendar_id = await _get_doctor_calendar_id(doctor)
    if not calendar_id:
        raise BookingError("calendário do médico não encontrado")

    now_iso = datetime.now(TZ).isoformat()
    fee, kind = _fee_fields(req, now_iso)
    ctype = "primeira_consulta" if req["first_consultation"] else None
    if not req["first_consultation"] and _is_minor_julio(patient, doctor):
        ctype = "acompanhamento"

    created: list[tuple[str, datetime, int, str]] = []
    try:
        for part, note in zip(req["parts"], _part_notes(req)):
            event_id = await create_event(
                calendar_id=calendar_id, start=part["start"], slot_minutes=part["minutes"],
                patient_name=display, doctor_name=DOCTOR_LABELS[doctor], session_note=note,
                modality=req["modality"], patient_email=patient.get("email") or "",
                patient_number=req["phone"],
            )
            created.append((event_id, part["start"], part["minutes"], note))
        client = await get_supabase()
        for event_id, start, minutes, note in created:
            await client.from_("appointments").insert({
                "patient_id": patient["id"],
                "contact_id": req["contact_id"],
                "doctor_id": DOCTOR_IDS[doctor],
                "appointment_id": event_id,
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=minutes)).isoformat(),
                "status": "scheduled",
                "modality": req["modality"],
                "consultation_type": ctype,
                "session_note": note or None,
                **fee,
            }).execute()
    except Exception as exc:
        _logger.exception("panel create_appointments falhou patient=%s", patient.get("id"))
        for event_id, *_ in created:
            try:
                await cancel_event(calendar_id, event_id)
            except Exception:
                _logger.exception("rollback: falha ao apagar evento %s", event_id)
        # Linhas já inseridas antes da falha (split): marcar como canceladas.
        try:
            client = await get_supabase()
            for event_id, *_ in created:
                await client.from_("appointments").update({"status": "canceled"}).eq("appointment_id", event_id).execute()
        except Exception:
            _logger.exception("rollback: falha ao cancelar linhas")
        raise BookingError("não foi possível gravar a consulta") from exc

    lines = [format_appt_line(doctor, start, note) for _, start, _, note in created]
    for event_id, start, minutes, note in created:
        await log_event("appointment_booked", req["phone"], {
            "doctor": doctor, "datetime": start.replace(tzinfo=None).isoformat(),
            "duration_minutes": minutes, "patient_name": name, "session_note": note,
            "origem": "painel", "atendente": req.get("agent") or "", "encaixe": bool(req.get("encaixe")),
            "appointment_id": event_id,
        })
    _notify_clinic_async(
        f"Agendamento realizado — {display}",
        "Agendamento realizado pelo painel ✅\n"
        f"Paciente: {display}\n" + "\n".join(lines) +
        f"\nModalidade: {req['modality']}\nAtendente: {req.get('agent') or '—'}"
        + ("\n⚠️ Encaixe fora da grade" if req.get("encaixe") else ""),
        req["phone"],
    )
    return {
        "kind": kind,
        "lines": lines,
        "pending_part2": bool(req.get("split")) and len(created) == 1,
        "appointments": [
            {"appointment_id": e, "start": s.isoformat(), "minutes": m, "session_note": n,
             "contact_id": req["contact_id"]}
            for e, s, m, n in created
        ],
    }
