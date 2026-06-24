"""Deterministic calendar helpers — weekday and relative-day labels in pt-BR.

LLMs make calendar-arithmetic mistakes, so all weekday / hoje-amanhã reasoning
is computed here and injected into prompts as ready-made labels.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")

# How many days ahead the reference calendar block lists (besides today).
REFERENCE_WINDOW_DAYS = 35

_WEEKDAYS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


def weekday_pt(d: date) -> str:
    """Portuguese weekday name, e.g. 'terça-feira'."""
    return _WEEKDAYS_PT[d.weekday()]


def relative_label(target: date, today: date) -> str | None:
    """'hoje' / 'amanhã' / 'depois de amanhã' for near dates, else None."""
    delta = (target - today).days
    if delta == 0:
        return "hoje"
    if delta == 1:
        return "amanhã"
    if delta == 2:
        return "depois de amanhã"
    return None


def date_suffix_pt(target: date, today: date) -> str:
    """Parenthetical content: 'amanhã, quinta-feira' or just 'quinta-feira'."""
    rel = relative_label(target, today)
    wd = weekday_pt(target)
    return f"{rel}, {wd}" if rel else wd


def format_date_pt(target: date, today: date) -> str:
    """'25/06 (amanhã, quinta-feira)' or '10/07 (sexta-feira)'."""
    return f"{target.strftime('%d/%m')} ({date_suffix_pt(target, today)})"


def build_date_reference(now: datetime) -> str:
    """Reference block: current datetime header + a row per day for the next
    REFERENCE_WINDOW_DAYS days, each pre-labelled with its weekday."""
    if now.tzinfo is None:
        raise ValueError("build_date_reference requires a timezone-aware datetime")
    today = now.date()
    lines = [
        f"Data e hora atual (America/Recife): "
        f"{now.strftime('%d/%m/%Y %H:%M')} ({weekday_pt(today)}).",
        "",
        "CALENDÁRIO DE REFERÊNCIA — use SEMPRE estes rótulos prontos. NUNCA calcule",
        "dia da semana nem hoje/amanhã por conta própria:",
    ]
    for i in range(REFERENCE_WINDOW_DAYS + 1):
        d = today + timedelta(days=i)
        if i == 0:
            prefix = "hoje   = "
        elif i == 1:
            prefix = "amanhã = "
        else:
            prefix = " " * 9
        lines.append(f"  {prefix}{d.strftime('%d/%m')} ({weekday_pt(d)})")
    return "\n".join(lines)
