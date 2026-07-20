"""Tests for Google Calendar slot logic (pure logic + mocked Google API)."""
import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")

_real_dt = datetime


class _FrozenDT(_real_dt):
    """datetime subclass that returns a fixed 'now' so past-date tests pass."""
    @classmethod
    def now(cls, tz=None):
        return _real_dt(2026, 3, 22, 4, 0, tzinfo=tz) if tz else _real_dt(2026, 3, 22, 4, 0)


@pytest.fixture
def freeze_calendar_now():
    with patch("app.google_calendar.datetime", _FrozenDT):
        yield


# ── _parse_day ────────────────────────────────────────────────────────────────

def test_parse_day_iso_date():
    from app.google_calendar import _parse_day
    result = _parse_day("2026-03-23")
    assert result == date(2026, 3, 23)


def test_parse_day_today():
    from app.google_calendar import _parse_day
    result = _parse_day("hoje")
    today = datetime.now(TZ).date()
    assert result == today


def test_parse_day_tomorrow():
    from app.google_calendar import _parse_day
    from datetime import timedelta
    result = _parse_day("amanhã")
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    assert result == tomorrow


def test_parse_day_weekday_name_returns_future_date():
    from app.google_calendar import _parse_day
    result = _parse_day("segunda")
    assert result is not None
    assert result.weekday() == 0  # Monday
    assert result > datetime.now(TZ).date()


def test_parse_day_invalid_returns_none():
    from app.google_calendar import _parse_day
    assert _parse_day("ontem") is None
    assert _parse_day("bla bla") is None


# ── "próxima semana" explicit qualifier ────────────────────────────────────────
# Regression for Mayri/Matheus case (5581988851971, 2026-07-07): patient said
# "próxima semana" on a Tuesday about Wednesday, and Eva kept offering the
# Wednesday of the CURRENT week instead of skipping to next week.

class _FrozenDTTuesday(_real_dt):
    """'Today' = 2026-07-07, a Tuesday — same weekday as the real bug report."""
    @classmethod
    def now(cls, tz=None):
        return _real_dt(2026, 7, 7, 10, 0, tzinfo=tz) if tz else _real_dt(2026, 7, 7, 10, 0)


@pytest.fixture
def freeze_calendar_tuesday():
    with patch("app.google_calendar.datetime", _FrozenDTTuesday):
        yield


def test_parse_day_weekday_alone_returns_this_week_occurrence(freeze_calendar_tuesday):
    """Plain 'quarta' on a Tuesday still means tomorrow (this week) — unchanged."""
    from app.google_calendar import _parse_day
    result = _parse_day("quarta")
    assert result == date(2026, 7, 8)


def test_parse_day_explicit_next_week_skips_current_week(freeze_calendar_tuesday):
    """'quarta-feira da próxima semana' must skip this week's Wednesday (07/07+1)
    and land on next week's Wednesday instead."""
    from app.google_calendar import _parse_day
    result = _parse_day("quarta-feira da próxima semana")
    assert result == date(2026, 7, 15)


def test_parse_day_semana_que_vem_variant(freeze_calendar_tuesday):
    from app.google_calendar import _parse_day
    result = _parse_day("quarta semana que vem")
    assert result == date(2026, 7, 15)


def test_parse_day_semana_seguinte_variant(freeze_calendar_tuesday):
    from app.google_calendar import _parse_day
    result = _parse_day("quarta da semana seguinte")
    assert result == date(2026, 7, 15)


# ── get_available_slots (with mocked Google API) ──────────────────────────────

def _make_service(busy_periods: list[dict]) -> MagicMock:
    """Build a mock Google Calendar service that returns the given busy list.

    busy_periods: list of {"start": iso_str, "end": iso_str} dicts.
    These are now returned via events().list() (not freebusy) so we convert
    each period to a minimal calendar event object.
    """
    events_items = [
        {
            "status": "confirmed",
            "summary": "Consulta — Paciente Teste",
            "start": {"dateTime": p["start"]},
            "end": {"dateTime": p["end"]},
        }
        for p in busy_periods
    ]
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": events_items}
    return service


async def test_slots_60min_julio_monday_morning(freeze_calendar_now):
    """Dr. Júlio works Mon 9-12; expect three 60-min slots when calendar is free."""
    from app.google_calendar import get_available_slots

    service = _make_service([])  # no busy periods
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # a known Monday
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )
    assert len(slots) == 3
    assert all(dt.weekday() == 0 for dt, _ in slots)
    assert slots[0][0].hour == 9


async def test_slots_120min_julio_monday(freeze_calendar_now):
    """120-min slots on Mon 9-12 → two slots fit: 9:00-11:00 and 10:00-12:00."""
    from app.google_calendar import get_available_slots

    service = _make_service([])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # Monday
            preferred_shift="manha",
            slot_minutes=120,
            doctor_key="julio",
        )
    # 9-12 window with 120-min slots: loop advances by 1h to find all starting
    # points. 9:00+2h=11:00≤12:00 ✓, 10:00+2h=12:00≤12:00 ✓, 11:00+2h=13:00>12:00 stop.
    assert len(slots) == 2
    assert slots[0][0].hour == 9
    assert slots[1][0].hour == 10


async def test_slots_empty_on_off_day():
    """Dr. Júlio doesn't work on Saturdays."""
    from app.google_calendar import get_available_slots

    service = _make_service([])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-21",  # Saturday
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )
    assert slots == []


async def test_busy_period_removes_slot(freeze_calendar_now):
    """A busy period that overlaps a slot must exclude that slot."""
    from app.google_calendar import get_available_slots

    # Block 9:00-10:00 on Monday
    busy = [{"start": "2026-03-23T09:00:00-03:00", "end": "2026-03-23T10:00:00-03:00"}]
    service = _make_service(busy)
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )
    hours = [dt.hour for dt, _ in slots]
    assert 9 not in hours
    assert 10 in hours


async def test_bruna_wednesday_returns_slots(freeze_calendar_now):
    """Dra. Bruna works Wed 8-12 and 14-18; both windows should produce slots."""
    from app.google_calendar import get_available_slots

    service = _make_service([])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-25",  # Wednesday
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="bruna",
        )
    assert len(slots) > 0
    assert all(dt.weekday() == 2 for dt, _ in slots)  # Wednesday


async def test_bruna_monday_morning_no_mid_morning_slots(freeze_calendar_now):
    """Dra. Bruna on Monday works 07:30-08:30 and 16:30-18:30 only.
    Requesting 'manha' must NOT return 9h/10h/11h slots."""
    from app.google_calendar import get_available_slots

    service = _make_service([])  # empty calendar
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # Monday
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="bruna",
        )
    hours = [dt.hour for dt, _ in slots]
    # 9, 10, 11 must never appear — Bruna's Monday morning window is 07:30-08:30
    for bad_hour in (9, 10, 11, 12, 13, 14, 15):
        assert bad_hour not in hours, f"Unexpected slot at {bad_hour}h for Bruna on Monday"


async def test_timezone_america_recife(freeze_calendar_now):
    """Returned slots must carry America/Recife tzinfo."""
    from app.google_calendar import get_available_slots

    service = _make_service([])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )
    assert all(dt.tzinfo is not None for dt, _ in slots)
    assert all(str(dt.tzinfo) == "America/Recife" for dt, _ in slots)


# ── format_doctor_schedules ────────────────────────────────────────────────────

def test_format_schedules_shows_regular_when_exception_blocks_day():
    """Blocked exception days must NOT hide the regular weekday schedule.

    When SCHEDULE_EXCEPTIONS has an empty-list entry for a weekday (blocking that
    specific date), format_doctor_schedules must still show the regular schedule
    for that weekday so the LLM knows the doctor is available on other occurrences.
    Without this fix the LLM would think the doctor never works that weekday.
    """
    from app.google_calendar import format_doctor_schedules

    # Freeze 'today' inside google_calendar to 2026-05-28 so the June 1-4
    # exceptions fall within the 14-day look-ahead window.
    with patch("app.google_calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 28)
        mock_date.fromisoformat = date.fromisoformat
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        text = format_doctor_schedules()

    # Parse out Dr. Júlio's section only (lines after "Dr. Júlio:")
    text_lines = text.splitlines()
    julio_start = next(i for i, l in enumerate(text_lines) if "Dr. Júlio" in l)
    julio_lines = text_lines[julio_start + 1:]

    # Quinta should show the regular schedule (manhã, tarde, noite) AND note
    # the exception — NOT just "SEM ATENDIMENTO" with no regular schedule.
    quinta_line = next(l for l in julio_lines if "Quinta" in l)
    assert "manhã" in quinta_line, f"Regular Thursday schedule missing: {quinta_line!r}"
    assert "EXCETO 04/06" in quinta_line, f"Exception note missing: {quinta_line!r}"
    assert "sem atendimento nesta data" in quinta_line

    # Quarta should show regular schedule + exception note
    quarta_line = next(l for l in julio_lines if "Quarta" in l)
    assert "manhã" in quarta_line, f"Regular Wednesday schedule missing: {quarta_line!r}"
    assert "EXCETO 03/06" in quarta_line


def test_format_schedules_extended_exception_shows_regular_too():
    """Extended-schedule exceptions must show both regular and exception windows."""
    from app.google_calendar import format_doctor_schedules

    with patch("app.google_calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 28)
        mock_date.fromisoformat = date.fromisoformat
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        text = format_doctor_schedules()

    # Segunda has an extended schedule on 01/06 (adds afternoon).
    # The line should show the regular morning schedule AND note the June 1 exception.
    segunda_line = next(l for l in text.splitlines() if "Segunda" in l and "Dr. Júlio" not in l)
    julio_lines = [l for l in text.splitlines() if "Segunda" in l]
    # Find the Dr. Júlio Segunda line (comes after "Dr. Júlio:" header)
    text_lines = text.splitlines()
    julio_idx = next(i for i, l in enumerate(text_lines) if "Dr. Júlio" in l)
    julio_segunda = next(l for l in text_lines[julio_idx:] if "Segunda" in l)
    assert "manhã" in julio_segunda, f"Regular Monday schedule missing: {julio_segunda!r}"
    assert "em 01/06" in julio_segunda, f"Exception date missing: {julio_segunda!r}"


def test_format_schedules_no_exceptions_clean():
    """After exceptions have expired, output shows plain regular schedules."""
    from app.google_calendar import format_doctor_schedules

    # August 22 — last known exception is 07/08 (bloqueado), next is 07/09, both outside the 14-day window
    with patch("app.google_calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 22)
        mock_date.fromisoformat = date.fromisoformat
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        text = format_doctor_schedules()

    assert "EXCETO" not in text
    assert "SEM ATENDIMENTO" not in text
    assert "exceção" not in text
    # Regular Thursday schedule should be present
    quinta_line = next(l for l in text.splitlines() if "Quinta" in l)
    assert "manhã" in quinta_line
