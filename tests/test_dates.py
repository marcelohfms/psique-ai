"""Unit tests for app/dates.py — deterministic calendar helpers."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.dates import (
    weekday_pt,
    relative_label,
    date_suffix_pt,
    format_date_pt,
    build_date_reference,
    REFERENCE_WINDOW_DAYS,
)

TZ = ZoneInfo("America/Recife")


def test_weekday_pt_known_dates():
    # 2026-06-24 is a Wednesday; 2026-06-23 a Tuesday; 2026-06-28 a Sunday
    assert weekday_pt(date(2026, 6, 24)) == "quarta-feira"
    assert weekday_pt(date(2026, 6, 23)) == "terça-feira"
    assert weekday_pt(date(2026, 6, 28)) == "domingo"


def test_relative_label_near_dates():
    today = date(2026, 6, 24)
    assert relative_label(date(2026, 6, 24), today) == "hoje"
    assert relative_label(date(2026, 6, 25), today) == "amanhã"
    assert relative_label(date(2026, 6, 26), today) == "depois de amanhã"
    assert relative_label(date(2026, 6, 27), today) is None
    assert relative_label(date(2026, 6, 23), today) is None


def test_date_suffix_combines_relative_and_weekday():
    today = date(2026, 6, 24)
    assert date_suffix_pt(date(2026, 6, 25), today) == "amanhã, quinta-feira"
    assert date_suffix_pt(date(2026, 7, 10), today) == "sexta-feira"


def test_format_date_pt():
    today = date(2026, 6, 24)
    assert format_date_pt(date(2026, 6, 25), today) == "25/06 (amanhã, quinta-feira)"
    assert format_date_pt(date(2026, 7, 10), today) == "10/07 (sexta-feira)"


def test_build_date_reference_structure():
    now = datetime(2026, 6, 24, 14, 30, tzinfo=TZ)
    block = build_date_reference(now)
    # Header line with current date + weekday
    assert "Data e hora atual (America/Recife): 24/06/2026 14:30 (quarta-feira)." in block
    assert "CALENDÁRIO DE REFERÊNCIA" in block
    # hoje / amanhã rows present and correctly labelled
    assert "hoje   = 24/06 (quarta-feira)" in block
    assert "amanhã = 25/06 (quinta-feira)" in block
    # Window covers today + REFERENCE_WINDOW_DAYS days (one row each)
    last_day = (now.date()).toordinal() + REFERENCE_WINDOW_DAYS
    from datetime import date as _date
    assert _date.fromordinal(last_day).strftime("%d/%m") in block


def test_build_date_reference_month_rollover():
    # 35 days ahead of 2026-06-24 is 2026-07-29
    now = datetime(2026, 6, 24, 9, 0, tzinfo=TZ)
    block = build_date_reference(now)
    assert "29/07 (quarta-feira)" in block


def test_build_date_reference_rejects_naive_datetime():
    with pytest.raises(ValueError):
        build_date_reference(datetime(2026, 6, 24, 9, 0))  # naive, no tzinfo
