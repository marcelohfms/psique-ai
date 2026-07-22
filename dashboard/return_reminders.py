"""Classificação de retorno periódico do paciente (dashboard /retornos).

Autocontido: não importa app/ (a imagem Docker do dashboard não contém app/).
"""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from payments import DOCTOR_KEY

_TZ = ZoneInfo("America/Recife")

RETURN_INTERVALS = ("15_dias", "1_mes", "3_meses", "6_meses")

RETURN_INTERVAL_LABELS = {
    "15_dias": "15 dias",
    "1_mes": "1 mês",
    "3_meses": "3 meses",
    "6_meses": "6 meses",
}

DOCTOR_ID_BY_KEY = {key: doctor_id for doctor_id, key in DOCTOR_KEY.items()}


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def compute_next_return_date(appointment_date: date, return_interval: str) -> date:
    """Calcula next_return_date a partir da data da consulta e do intervalo escolhido pelo médico."""
    if return_interval == "15_dias":
        return appointment_date + timedelta(days=15)
    if return_interval == "1_mes":
        return _add_months(appointment_date, 1)
    if return_interval == "3_meses":
        return _add_months(appointment_date, 3)
    if return_interval == "6_meses":
        return _add_months(appointment_date, 6)
    raise ValueError(f"return_interval inválido: {return_interval!r}")


async def get_today_appointments(client, doctor_id: str, today: date | None = None) -> list[dict]:
    """Pacientes com consulta hoje para o médico — sempre visível, para classificar na hora."""
    if today is None:
        today = datetime.now(_TZ).date()
    start = datetime(today.year, today.month, today.day, tzinfo=_TZ)
    end = start + timedelta(days=1)
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .gte("start_time", start.isoformat())
        .lt("start_time", end.isoformat())
        .order("start_time")
        .execute()
    )
    return result.data or []


async def get_pending_classification(client, doctor_id: str) -> list[dict]:
    """Pacientes cuja consulta concluída mais recente ainda não foi classificada.

    Ordenados da mais antiga para a mais nova (fila que vai esvaziando).
    """
    appts_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .eq("status", "completed")
        .order("start_time")
        .execute()
    )
    latest_by_patient: dict[str, dict] = {}
    for appt in appts_result.data or []:
        patient_id = appt.get("patient_id")
        if not patient_id:
            continue
        current = latest_by_patient.get(patient_id)
        if current is None or appt["start_time"] > current["start_time"]:
            latest_by_patient[patient_id] = appt

    if not latest_by_patient:
        return []

    rr_result = await (
        client.from_("return_reminders")
        .select("patient_id, last_classified_appointment_id")
        .in_("patient_id", list(latest_by_patient.keys()))
        .execute()
    )
    classified = {
        row["patient_id"]: row.get("last_classified_appointment_id")
        for row in (rr_result.data or [])
    }

    pending = [
        appt for appt in latest_by_patient.values()
        if classified.get(appt["patient_id"]) != appt["appointment_id"]
    ]
    pending.sort(key=lambda a: a["start_time"])
    return pending
