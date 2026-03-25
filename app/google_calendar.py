import asyncio
import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TIMEZONE = "America/Recife"
TZ = ZoneInfo(TIMEZONE)

# Working hours per doctor per weekday (0=Mon … 6=Sun).
# Each entry is a list of (start_h, start_m, end_h, end_m) tuples.
# Edit here to change doctor availability — no other file needs to change.
DOCTOR_SCHEDULES: dict[str, dict[int, list[tuple[int, int, int, int]]]] = {
    "bruna": {
        0: [(11, 30, 12, 30), (15, 30, 16, 30)],  # Segunda
        2: [(8, 0, 12, 0), (14, 0, 18, 0)],        # Quarta
        4: [(8, 0, 12, 0), (13, 0, 16, 0)],        # Sexta
    },
    "julio": {
        0: [(9, 0, 12, 0)],                                   # Segunda
        1: [(13, 0, 18, 0)],                                  # Terça
        2: [(9, 0, 12, 0)],                                   # Quarta
        3: [(9, 0, 12, 0), (14, 0, 17, 0), (18, 0, 20, 0)],  # Quinta
    },
}

_WEEKDAY_NAMES = {0: "Segunda", 1: "Terça", 2: "Quarta", 3: "Quinta", 4: "Sexta", 5: "Sábado", 6: "Domingo"}
_DOCTOR_LABELS = {"bruna": "Dra. Bruna", "julio": "Dr. Júlio"}


def _shift_label(start_h: int, end_h: int) -> str:
    """Map a time window to a generic shift name in Portuguese."""
    if end_h <= 12:
        return "manhã"
    if start_h >= 18:
        return "noite"
    if start_h >= 12:
        return "tarde"
    if start_h < 12 and end_h > 12:
        return "manhã e tarde"
    return "manhã"


def format_doctor_schedules() -> str:
    """Return a natural-language Portuguese summary of all doctor schedules.
    Reads directly from DOCTOR_SCHEDULES so any edit there is reflected here.
    Describes shifts generically (manhã/tarde/noite) rather than exact hours.
    """
    lines = []
    for doctor_key, days in DOCTOR_SCHEDULES.items():
        label = _DOCTOR_LABELS.get(doctor_key, doctor_key)
        lines.append(f"{label}:")
        for weekday in sorted(days):
            windows = days[weekday]
            shifts = []
            seen = set()
            for sh, sm, eh, em in windows:
                s = _shift_label(sh, eh)
                if s not in seen:
                    shifts.append(s)
                    seen.add(s)
            lines.append(f"  - {_WEEKDAY_NAMES[weekday]}: {', '.join(shifts)}")
    return "\n".join(lines)


SHIFT_HOURS: dict[str, tuple[int, int]] = {
    "manha":  (8, 12),
    "tarde":  (13, 18),
    "noite":  (18, 21),
}

_WEEKDAYS_PT: dict[str, int] = {
    "segunda": 0, "terca": 1, "terça": 1,
    "quarta": 2,  "quinta": 3, "sexta": 4,
    "sabado": 5,  "sábado": 5, "domingo": 6,
}


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/calendar"],
    )


def _next_weekday(weekday: int) -> date:
    today = datetime.now(TZ).date()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _parse_day(preferred_day: str) -> date | None:
    s = preferred_day.lower().strip()
    today = datetime.now(TZ).date()

    if s in ("hoje", "today"):
        return today
    if s in ("amanha", "amanhã", "tomorrow"):
        return today + timedelta(days=1)
    for name, wd in _WEEKDAYS_PT.items():
        if name in s:
            return _next_weekday(wd)
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _normalize_shift(shift: str) -> str:
    return shift.lower().replace("ã", "a").replace("manhã", "manha").strip()


def _get_busy(service, calendar_id: str, window_start: datetime, window_end: datetime) -> list:
    body = {
        "timeMin": window_start.isoformat(),
        "timeMax": window_end.isoformat(),
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    return result["calendars"][calendar_id]["busy"]


def _create_event(service, calendar_id: str, event: dict) -> str:
    # Temporary diagnostic: log which account owns this calendar
    try:
        cal_info = service.calendars().get(calendarId=calendar_id).execute()
        import logging
        logging.getLogger(__name__).warning("CALENDAR_OWNER: %s", cal_info.get("id"))
    except Exception:
        pass
    result = service.events().insert(calendarId=calendar_id, body=event).execute()
    return result["id"]


def _cancel_event(service, calendar_id: str, event_id: str) -> None:
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


def _update_event(service, calendar_id: str, event_id: str, patch: dict) -> None:
    service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch).execute()


async def get_available_slots(
    calendar_id: str,
    preferred_day: str,
    preferred_shift: str,
    slot_minutes: int = 60,
    doctor_key: str | None = None,
) -> list[datetime]:
    """Return list of available slot start times."""
    target_date = _parse_day(preferred_day)
    if not target_date:
        return []

    weekday = target_date.weekday()
    slot_delta = timedelta(minutes=slot_minutes)

    # Build working windows: use doctor schedule if available, else fall back to shift
    doctor_schedule = DOCTOR_SCHEDULES.get(doctor_key or "", {})
    shift = _normalize_shift(preferred_shift)
    shift_start_h, shift_end_h = SHIFT_HOURS.get(shift, (8, 18))

    if doctor_schedule:
        day_windows_raw = doctor_schedule.get(weekday)
        if day_windows_raw is None:
            return []  # doctor doesn't work on this day
        # Keep only windows that overlap with the requested shift
        filtered = [
            (sh, sm, eh, em) for sh, sm, eh, em in day_windows_raw
            if sh < shift_end_h and eh > shift_start_h
        ]
        if not filtered:
            return []  # doctor doesn't work this shift on this day
        windows = [
            (
                datetime(target_date.year, target_date.month, target_date.day, sh, sm, tzinfo=TZ),
                datetime(target_date.year, target_date.month, target_date.day, eh, em, tzinfo=TZ),
            )
            for sh, sm, eh, em in filtered
        ]
    else:
        start_hour, end_hour = shift_start_h, shift_end_h
        windows = [(
            datetime(target_date.year, target_date.month, target_date.day, start_hour, 0, tzinfo=TZ),
            datetime(target_date.year, target_date.month, target_date.day, end_hour, 0, tzinfo=TZ),
        )]

    # Fetch busy times covering the full span of all windows
    overall_start = min(w[0] for w in windows)
    overall_end = max(w[1] for w in windows)

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    busy_raw = await loop.run_in_executor(
        None, _get_busy, service, calendar_id, overall_start, overall_end
    )

    busy_ranges = [
        (
            datetime.fromisoformat(b["start"]).astimezone(TZ),
            datetime.fromisoformat(b["end"]).astimezone(TZ),
        )
        for b in busy_raw
    ]

    slots: list[datetime] = []
    for window_start, window_end in windows:
        current = window_start
        while current + slot_delta <= window_end:
            slot_end = current + slot_delta
            if not any(current < be and slot_end > bs for bs, be in busy_ranges):
                slots.append(current)
            current += slot_delta

    slots.sort()
    return slots


async def create_event(
    calendar_id: str,
    start: datetime,
    slot_minutes: int,
    patient_name: str,
    doctor_name: str,
    is_minor_first: bool = False,
    session_note: str = "",
) -> str:
    """Create a Google Calendar event and return the event ID."""
    end = start + timedelta(minutes=slot_minutes)
    description = f"Paciente: {patient_name}\nMédico: {doctor_name}"
    if session_note:
        description += f"\n\n{session_note}"
    elif is_minor_first:
        description += "\n\n1ª hora: conversa com os pais/responsáveis\n2ª hora: consulta com o paciente"

    summary = f"Consulta — {patient_name}"
    if session_note:
        summary += f" ({session_note})"

    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
    }

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    event_id = await loop.run_in_executor(
        None, _create_event, service, calendar_id, event
    )
    return event_id


async def cancel_event(calendar_id: str, event_id: str) -> None:
    """Delete a Google Calendar event."""
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _cancel_event, service, calendar_id, event_id)


async def update_event(
    calendar_id: str,
    event_id: str,
    new_start: datetime,
    slot_minutes: int,
    patient_name: str,
    doctor_name: str,
    is_minor_first: bool = False,
) -> None:
    """Patch an existing Google Calendar event with a new start/end time."""
    new_end = new_start + timedelta(minutes=slot_minutes)
    description = f"Paciente: {patient_name}\nMédico: {doctor_name}"
    if is_minor_first:
        description += "\n\n1ª hora: conversa com os pais/responsáveis\n2ª hora: consulta com o paciente"

    patch = {
        "start": {"dateTime": new_start.isoformat(), "timeZone": TIMEZONE},
        "end":   {"dateTime": new_end.isoformat(),   "timeZone": TIMEZONE},
        "description": description,
    }
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update_event, service, calendar_id, event_id, patch)
