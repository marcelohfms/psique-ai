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
