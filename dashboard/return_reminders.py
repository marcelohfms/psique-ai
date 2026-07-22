"""Classificação de retorno periódico do paciente (dashboard /retornos).

Autocontido: não importa app/ (a imagem Docker do dashboard não contém app/).
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from payments import DOCTOR_KEY

_TZ = ZoneInfo("America/Recife")

RETURN_INTERVALS = ("15_dias", "1_mes", "2_meses", "3_meses", "4_meses", "6_meses")

RETURN_INTERVAL_LABELS = {
    "15_dias": "15 dias",
    "1_mes": "1 mês",
    "2_meses": "2 meses",
    "3_meses": "3 meses",
    "4_meses": "4 meses",
    "6_meses": "6 meses",
}

DOCTOR_ID_BY_KEY = {key: doctor_id for doctor_id, key in DOCTOR_KEY.items()}


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _local_date_time(start_time: str | None) -> tuple[str, str]:
    """Converte start_time (ISO, vem em UTC do Postgres) pro fuso de Recife.

    Retorna (data 'aaaa-mm-dd', hora 'HH:MM') — ambos "" se start_time for
    None/vazio. Sem isso, tanto a exibição quanto a data enviada ao salvar a
    classificação ficam erradas perto da virada do dia (uma consulta às 22h
    em Recife já é dia seguinte em UTC) — mesma classe de bug já corrigida
    no FakeQuery de teste (ver dashboard/tests/conftest.py).
    """
    if not start_time:
        return "", ""
    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00")).astimezone(_TZ)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def _with_local_date_time(appointments: list[dict]) -> list[dict]:
    for appt in appointments:
        appt["local_date"], appt["local_time"] = _local_date_time(appt.get("start_time"))
    return appointments


def compute_next_return_date(appointment_date: date, return_interval: str) -> date:
    """Calcula next_return_date a partir da data da consulta e do intervalo escolhido pelo médico."""
    if return_interval == "15_dias":
        return appointment_date + timedelta(days=15)
    if return_interval == "1_mes":
        return _add_months(appointment_date, 1)
    if return_interval == "2_meses":
        return _add_months(appointment_date, 2)
    if return_interval == "3_meses":
        return _add_months(appointment_date, 3)
    if return_interval == "4_meses":
        return _add_months(appointment_date, 4)
    if return_interval == "6_meses":
        return _add_months(appointment_date, 6)
    raise ValueError(f"return_interval inválido: {return_interval!r}")


async def get_today_appointments(client, doctor_id: str, today: date | None = None) -> list[dict]:
    """Pacientes com consulta hoje para o médico — sempre visível, para classificar na hora.

    Exclui `status = 'canceled'`: um reagendamento no mesmo dia deixa o slot
    antigo cancelado e cria um novo — sem esse filtro, os dois aparecem e o
    paciente fica duplicado na lista (caso Jonas Leonardo, 2026-07-22).
    """
    if today is None:
        today = datetime.now(_TZ).date()
    start = datetime(today.year, today.month, today.day, tzinfo=_TZ)
    end = start + timedelta(days=1)
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .neq("status", "canceled")
        .gte("start_time", start.isoformat())
        .lt("start_time", end.isoformat())
        .order("start_time")
        .execute()
    )
    return _with_local_date_time(result.data or [])


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
    return _with_local_date_time(pending)


async def save_classification(
    client,
    patient_id: str,
    doctor_id: str,
    appointment_id: str,
    appointment_date: date,
    return_interval: str,
) -> dict:
    """Grava/atualiza a classificação de retorno de um paciente (1 linha por paciente).

    Zera as flags de envio (novo ciclo). Casos especiais: `15_dias` já marca
    as 3 flags como enviadas (intervalo curto demais para os lembretes
    mensais fazerem sentido); `1_mes` já marca `month_before_sent_at` (o
    mês-alvo é sempre o mês seguinte à própria classificação, então "um mês
    antes" sairia colado na consulta que acabou de acontecer).
    """
    if return_interval not in RETURN_INTERVALS:
        raise ValueError(f"return_interval inválido: {return_interval!r}")

    next_return_date = compute_next_return_date(appointment_date, return_interval)
    now = datetime.now(_TZ).isoformat()

    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "return_interval": return_interval,
        "next_return_date": next_return_date.isoformat(),
        "last_classified_appointment_id": appointment_id,
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
        "updated_at": now,
    }
    if return_interval == "15_dias":
        payload["month_before_sent_at"] = now
        payload["month_of_sent_at"] = now
        payload["overdue_sent_at"] = now
    elif return_interval == "1_mes":
        payload["month_before_sent_at"] = now

    existing = await (
        client.from_("return_reminders").select("id").eq("patient_id", patient_id).execute()
    )
    if existing.data:
        result = await (
            client.from_("return_reminders").update(payload).eq("patient_id", patient_id).execute()
        )
    else:
        result = await client.from_("return_reminders").insert(payload).execute()
    return (result.data or [payload])[0]
