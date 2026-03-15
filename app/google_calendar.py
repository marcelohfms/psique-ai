import asyncio
import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TIMEZONE = "America/Recife"
TZ = ZoneInfo(TIMEZONE)

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
) -> list[datetime]:
    """Return list of available slot start times."""
    target_date = _parse_day(preferred_day)
    if not target_date:
        return []

    shift = _normalize_shift(preferred_shift)
    start_hour, end_hour = SHIFT_HOURS.get(shift, (8, 18))

    window_start = datetime(target_date.year, target_date.month, target_date.day,
                            start_hour, 0, tzinfo=TZ)
    window_end = datetime(target_date.year, target_date.month, target_date.day,
                          end_hour, 0, tzinfo=TZ)

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    # Run blocking API call in thread pool
    loop = asyncio.get_event_loop()
    busy_raw = await loop.run_in_executor(
        None, _get_busy, service, calendar_id, window_start, window_end
    )

    busy_ranges = [
        (
            datetime.fromisoformat(b["start"]).astimezone(TZ),
            datetime.fromisoformat(b["end"]).astimezone(TZ),
        )
        for b in busy_raw
    ]

    slot_delta = timedelta(minutes=slot_minutes)
    slots: list[datetime] = []
    current = window_start
    while current + slot_delta <= window_end:
        slot_end = current + slot_delta
        if not any(current < be and slot_end > bs for bs, be in busy_ranges):
            slots.append(current)
        current += slot_delta

    return slots


async def create_event(
    calendar_id: str,
    start: datetime,
    slot_minutes: int,
    patient_name: str,
    doctor_name: str,
    is_minor_first: bool = False,
) -> str:
    """Create a Google Calendar event and return the event ID."""
    end = start + timedelta(minutes=slot_minutes)
    description = f"Paciente: {patient_name}\nMédico: {doctor_name}"
    if is_minor_first:
        description += "\n\n1ª hora: conversa com os pais/responsáveis\n2ª hora: consulta com o paciente"

    event = {
        "summary": f"Consulta — {patient_name}",
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
