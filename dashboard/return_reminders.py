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
