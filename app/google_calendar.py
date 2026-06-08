import asyncio
import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TIMEZONE = "America/Recife"
TZ = ZoneInfo(TIMEZONE)

# Working hours per doctor per weekday (0=Mon … 6=Sun).
# Each entry: (start_h, start_m, end_h, end_m, modality)
# modality values:
#   "online"                  — slot is exclusively online (no choice)
#   "escolha"                 — patient chooses online or presencial
#   "presencial_sob_consulta" — presencial possible but requires human confirmation
# Edit here to change doctor availability — no other file needs to change.
# Date-specific exceptions that override DOCTOR_SCHEDULES for a single day.
# Key: "YYYY-MM-DD", Value: list of windows (same tuple format as DOCTOR_SCHEDULES).
# Empty list means the doctor does not work on that date.
SCHEDULE_EXCEPTIONS: dict[str, dict[str, list[tuple[int, int, int, int, str]]]] = {
    "julio": {
        "2026-06-01": [(9, 0, 12, 0, "escolha"), (14, 0, 19, 0, "escolha")],  # Segunda: adiciona tarde
        "2026-06-02": [],                                                       # Terça: sem atendimento
        "2026-06-03": [],                                                       # Quarta: sem atendimento
        "2026-06-04": [],                                                       # Quinta: sem atendimento
        "2026-06-24": [],                                                       # Quarta: sem atendimento
    },
    "bruna": {
        "2026-05-22": [],  # Sexta: sem atendimento
        "2026-06-22": [],  # Segunda: sem atendimento
        "2026-06-23": [],  # Terça: sem atendimento
        "2026-06-24": [],  # Quarta: sem atendimento
    },
}

DOCTOR_SCHEDULES: dict[str, dict[int, list[tuple[int, int, int, int, str]]]] = {
    "bruna": {
        0: [(7, 30, 8, 30, "online"), (16, 30, 18, 30, "online")],   # Segunda — tudo online
        2: [(9, 0, 12, 0, "escolha"), (14, 0, 18, 0, "escolha")],    # Quarta — paciente escolhe
        4: [(8, 0, 12, 0, "escolha"), (13, 0, 16, 0, "online")],     # Sexta — manhã escolha, tarde online
    },
    "julio": {
        0: [(9, 0, 12, 0, "escolha")],                                                                   # Segunda
        1: [(13, 0, 18, 0, "escolha")],                                                                  # Terça
        2: [(9, 0, 12, 0, "escolha")],                                                                   # Quarta
        3: [(9, 0, 12, 0, "escolha"), (14, 0, 18, 0, "presencial_sob_consulta"), (18, 0, 20, 0, "escolha")],  # Quinta
    },
}

# Permanent schedule changes starting from a given date.
# Format: { doctor_key: [ (date_from, new_schedule_dict) ] }
# new_schedule_dict has the same format as DOCTOR_SCHEDULES (weekday → windows).
# The first entry whose date_from <= target_date applies (list is checked in reverse order).
SCHEDULE_CHANGES: dict[str, list[tuple[date, dict[int, list[tuple[int, int, int, int, str]]]]]] = {
    "julio": [
        (date(2026, 7, 1), {
            0: [(9, 0, 12, 0, "escolha"), (13, 0, 18, 0, "escolha")],  # Segunda: manhã + tarde (antes era só manhã)
            1: [],                                                        # Terça: sem atendimento (horários movidos para segunda)
            2: [(9, 0, 12, 0, "escolha")],                               # Quarta: inalterado
            3: [(9, 0, 12, 0, "escolha"), (14, 0, 18, 0, "presencial_sob_consulta"), (18, 0, 20, 0, "escolha")],  # Quinta: inalterado
        }),
    ],
}


def _get_doctor_schedule(doctor_key: str, target_date: date) -> dict[int, list[tuple[int, int, int, int, str]]]:
    """Return the correct weekly schedule for a doctor on a given date,
    applying any permanent schedule changes that took effect on or before that date."""
    base = DOCTOR_SCHEDULES.get(doctor_key, {})
    changes = SCHEDULE_CHANGES.get(doctor_key, [])
    # Apply changes in order — last applicable one wins
    for change_date, new_schedule in sorted(changes, key=lambda x: x[0]):
        if target_date >= change_date:
            base = new_schedule
    return base


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


_MODALITY_LABELS = {
    "online": "apenas online",
    "escolha": "online ou presencial",
    "presencial_sob_consulta": "online ou presencial sob consulta",
}


def format_doctor_schedules() -> str:
    """Return a natural-language Portuguese summary of all doctor schedules.
    Reads from DOCTOR_SCHEDULES and overlays SCHEDULE_EXCEPTIONS for the next
    14 days so Eva has accurate availability when guiding day selection.
    Describes shifts generically (manhã/tarde/noite) rather than exact hours,
    and includes modality info per window.
    """
    today = date.today()
    # Map each doctor's upcoming exceptions: weekday → (date_str, windows | None)
    upcoming_exceptions: dict[str, dict[int, tuple[str, list | None]]] = {}
    for doctor_key, exc_map in SCHEDULE_EXCEPTIONS.items():
        for date_str, windows in exc_map.items():
            exc_date = date.fromisoformat(date_str)
            if 0 <= (exc_date - today).days <= 14:
                if doctor_key not in upcoming_exceptions:
                    upcoming_exceptions[doctor_key] = {}
                upcoming_exceptions[doctor_key][exc_date.weekday()] = (date_str, windows or None)

    lines = []
    for doctor_key in DOCTOR_SCHEDULES:
        days = _get_doctor_schedule(doctor_key, today)
        label = _DOCTOR_LABELS.get(doctor_key, doctor_key)
        lines.append(f"{label}:")
        doc_exc = upcoming_exceptions.get(doctor_key, {})

        # Collect all weekdays: regular + exception-only days
        all_weekdays = sorted(set(days.keys()) | {wd for wd, (_, w) in doc_exc.items() if w})

        for weekday in all_weekdays:
            exc_entry = doc_exc.get(weekday)  # None means no exception this week

            # Build regular-schedule description for this weekday (used below)
            regular_windows = days.get(weekday, [])
            regular_parts: list[str] = []
            regular_seen: set = set()
            for entry in regular_windows:
                sh, sm, eh, em, modality = entry
                shift = _shift_label(sh, eh)
                mod = _MODALITY_LABELS.get(modality, modality)
                key = (shift, modality)
                if key not in regular_seen:
                    regular_parts.append(f"{shift} ({mod})")
                    regular_seen.add(key)
            regular_str = ", ".join(regular_parts) if regular_parts else "sem atendimento"

            if exc_entry is not None:
                exc_date_str, exc_windows = exc_entry
                exc_date_label = date.fromisoformat(exc_date_str).strftime("%d/%m")
                if exc_windows is None:
                    # Blocked on this specific date — still show the regular schedule so
                    # the LLM knows the doctor normally works on this weekday.
                    lines.append(
                        f"  - {_WEEKDAY_NAMES[weekday]}: {regular_str} "
                        f"[EXCETO {exc_date_label}: sem atendimento nesta data]"
                    )
                else:
                    # Different/extended schedule on this specific date — show both the
                    # exception windows and the regular schedule for other occurrences.
                    exc_parts: list[str] = []
                    exc_seen: set = set()
                    for entry in exc_windows:
                        sh, sm, eh, em, modality = entry
                        shift = _shift_label(sh, eh)
                        mod = _MODALITY_LABELS.get(modality, modality)
                        key = (shift, modality)
                        if key not in exc_seen:
                            exc_parts.append(f"{shift} ({mod})")
                            exc_seen.add(key)
                    lines.append(
                        f"  - {_WEEKDAY_NAMES[weekday]}: {regular_str} "
                        f"[em {exc_date_label}: {', '.join(exc_parts)}]"
                    )
            else:
                # Regular schedule — no upcoming exception
                lines.append(f"  - {_WEEKDAY_NAMES[weekday]}: {regular_str}")
    return "\n".join(lines)


def get_modality_for_slot(doctor_key: str, slot_dt: datetime) -> str:
    """Return the modality constraint for a given slot datetime."""
    weekday = slot_dt.weekday()
    windows = DOCTOR_SCHEDULES.get(doctor_key, {}).get(weekday, [])
    slot_min = slot_dt.hour * 60 + slot_dt.minute
    for entry in windows:
        sh, sm, eh, em, modality = entry
        if (sh * 60 + sm) <= slot_min < (eh * 60 + em):
            return modality
    return "escolha"


SHIFT_HOURS: dict[str, tuple[int, int]] = {
    "manha":  (7, 12),
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
        pass
    # Handle dd/mm format (e.g. "25/05", "25-05")
    try:
        parts = s.replace("-", "/").split("/")
        if len(parts) == 2:
            day, month = int(parts[0]), int(parts[1])
            year = today.year
            d = date(year, month, day)
            if d < today:
                d = date(year + 1, month, day)
            return d
    except (ValueError, IndexError):
        pass
    return None


def _normalize_shift(shift: str) -> str:
    return shift.lower().replace("ã", "a").replace("manhã", "manha").strip()


def _get_busy(service, calendar_id: str, window_start: datetime, window_end: datetime) -> list:
    """
    Return busy ranges using events().list() so that ALL events are considered,
    including those marked as 'free' (transparent) by external apps.
    freebusy() only returns opaque events, missing many external bookings.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=window_start.isoformat(),
        timeMax=window_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = result.get("items", [])
    busy = []
    for evt in events:
        # Skip cancelled events
        if evt.get("status") == "cancelled":
            continue
        start_raw = evt.get("start", {})
        end_raw = evt.get("end", {})
        # Skip all-day events (they use 'date' not 'dateTime')
        if "dateTime" not in start_raw:
            continue
        busy.append({"start": start_raw["dateTime"], "end": end_raw["dateTime"]})

    _logger.warning("EVENTS_BUSY calendar=%s window=%s→%s found=%d events=%s",
                    calendar_id, window_start, window_end, len(busy), busy)
    return busy


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
) -> list[tuple[datetime, str]]:
    """Return list of (slot_start, modality) pairs for available slots."""
    target_date = _parse_day(preferred_day)
    if not target_date:
        return []

    weekday = target_date.weekday()
    slot_delta = timedelta(minutes=slot_minutes)

    # Build working windows: use doctor schedule if available, else fall back to shift
    doctor_schedule = _get_doctor_schedule(doctor_key or "", target_date) if doctor_key else DOCTOR_SCHEDULES.get(doctor_key or "", {})
    shift = _normalize_shift(preferred_shift)
    shift_start_h, shift_end_h = SHIFT_HOURS.get(shift, (8, 18))

    # Check for date-specific exception first
    date_key = target_date.isoformat()
    doctor_exceptions = SCHEDULE_EXCEPTIONS.get(doctor_key or "", {})
    if date_key in doctor_exceptions:
        day_windows_raw = doctor_exceptions[date_key]  # may be [] (no work that day)
    elif doctor_schedule:
        day_windows_raw = doctor_schedule.get(weekday)
    else:
        day_windows_raw = None

    if doctor_schedule or date_key in doctor_exceptions:
        if day_windows_raw is None:
            return []  # doctor doesn't work on this day
        # Keep only windows that overlap with the requested shift
        filtered = [
            entry for entry in day_windows_raw
            if entry[0] < shift_end_h and entry[2] > shift_start_h
        ]
        if not filtered:
            return []  # doctor doesn't work this shift on this day
        shift_start_dt = datetime(target_date.year, target_date.month, target_date.day, shift_start_h, 0, tzinfo=TZ)
        shift_end_dt   = datetime(target_date.year, target_date.month, target_date.day, shift_end_h,   0, tzinfo=TZ)
        windows = []
        for entry in filtered:
            ws = datetime(target_date.year, target_date.month, target_date.day, entry[0], entry[1], tzinfo=TZ)
            we = datetime(target_date.year, target_date.month, target_date.day, entry[2], entry[3], tzinfo=TZ)
            # Clip window to the requested shift so a 14h–19h window doesn't bleed
            # into a "manhã" or "noite" query (avoids duplicate slots across shifts).
            ws = max(ws, shift_start_dt)
            we = min(we, shift_end_dt)
            if ws < we:
                windows.append((ws, we, entry[4]))
    else:
        start_hour, end_hour = shift_start_h, shift_end_h
        windows = [(
            datetime(target_date.year, target_date.month, target_date.day, start_hour, 0, tzinfo=TZ),
            datetime(target_date.year, target_date.month, target_date.day, end_hour, 0, tzinfo=TZ),
            "escolha",
        )]

    # Fetch busy times covering the full span of all windows
    overall_start = min(w[0] for w in windows)
    overall_end = max(w[1] for w in windows)

    import logging as _log2
    _log2.getLogger(__name__).warning(
        "GET_SLOTS_START doctor=%s calendar=%s date=%s windows=%s",
        doctor_key, calendar_id, target_date,
        [(w[0].strftime("%H:%M"), w[1].strftime("%H:%M")) for w in windows],
    )

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_running_loop()
    try:
        busy_raw = await loop.run_in_executor(
            None, _get_busy, service, calendar_id, overall_start, overall_end
        )
    except Exception as _e:
        _log2.getLogger(__name__).error("GET_SLOTS_BUSY_ERROR doctor=%s calendar=%s error=%s", doctor_key, calendar_id, _e)
        busy_raw = []

    busy_ranges = []
    for b in busy_raw:
        bs = datetime.fromisoformat(b["start"]).astimezone(TZ)
        be = datetime.fromisoformat(b["end"]).astimezone(TZ)
        # Events shorter than 1 minute (including zero-duration) are treated as 1h
        # so the containing slot is properly blocked.
        if (be - bs).total_seconds() < 60:
            be = bs + timedelta(hours=1)
        busy_ranges.append((bs, be))

    min_start = datetime.now(TZ) + timedelta(hours=4)

    # Always advance by 1h so we find every possible starting hour, even for
    # 2h slots (e.g. a 17h start for a 17h–19h block would be missed if we
    # advanced by slot_delta=2h and only checked 14h, 16h, 18h).
    advance = timedelta(hours=1)
    slots: list[tuple[datetime, str]] = []
    for window_start, window_end, modality in windows:
        current = window_start
        while current + slot_delta <= window_end:
            slot_end = current + slot_delta
            if current >= min_start and not any(current < be and slot_end > bs for bs, be in busy_ranges):
                slots.append((current, modality))
            current += advance

    # Safety net: discard any slot that falls outside the doctor's defined schedule
    # windows (catches edge-cases where doctor_key was missing or mismatched).
    if doctor_key and doctor_key in DOCTOR_SCHEDULES:
        validated: list[tuple[datetime, str]] = []
        exc_map = SCHEDULE_EXCEPTIONS.get(doctor_key, {})
        for slot_dt, mod in slots:
            slot_date_key = slot_dt.date().isoformat()
            if slot_date_key in exc_map:
                day_wins = exc_map[slot_date_key]
            else:
                day_wins = DOCTOR_SCHEDULES[doctor_key].get(slot_dt.weekday(), [])
            slot_min = slot_dt.hour * 60 + slot_dt.minute
            if any(
                (sh * 60 + sm) <= slot_min < (eh * 60 + em)
                for sh, sm, eh, em, _ in day_wins
            ):
                validated.append((slot_dt, mod))
        slots = validated

    slots.sort(key=lambda x: x[0])
    return slots


async def create_event(
    calendar_id: str,
    start: datetime,
    slot_minutes: int,
    patient_name: str,
    doctor_name: str,
    is_minor_first: bool = False,
    session_note: str = "",
    modality: str = "",
    patient_email: str = "",
    patient_number: str = "",
) -> str:
    """Create a Google Calendar event and return the event ID."""
    end = start + timedelta(minutes=slot_minutes)
    description = f"Paciente: {patient_name}\nMédico: {doctor_name}"
    if modality:
        modality_label = "Online" if modality == "online" else "Presencial"
        description += f"\nModalidade: {modality_label}"
    if patient_number:
        number_clean = patient_number.replace("@s.whatsapp.net", "")
        description += f"\nNúmero: {number_clean}"
    if patient_email:
        description += f"\nE-mail: {patient_email}"
    if session_note:
        description += f"\n\n{session_note}"
    elif is_minor_first:
        description += "\n\n1ª hora: conversa com os pais/responsáveis\n2ª hora: consulta com o paciente"

    summary = f"Consulta — {patient_name}"
    if modality:
        modality_label = "Online" if modality == "online" else "Presencial"
        summary += f" [{modality_label}]"
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
    loop = asyncio.get_running_loop()
    event_id = await loop.run_in_executor(
        None, _create_event, service, calendar_id, event
    )
    return event_id


async def cancel_event(calendar_id: str, event_id: str) -> None:
    """Delete a Google Calendar event."""
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _cancel_event, service, calendar_id, event_id)


async def update_event(
    calendar_id: str,
    event_id: str,
    new_start: datetime,
    slot_minutes: int,
    patient_name: str,
    doctor_name: str,
    is_minor_first: bool = False,
    modality: str = "",
    patient_email: str = "",
    patient_number: str = "",
) -> None:
    """Patch an existing Google Calendar event with a new start/end time."""
    new_end = new_start + timedelta(minutes=slot_minutes)
    description = f"Paciente: {patient_name}\nMédico: {doctor_name}"
    if modality:
        modality_label = "Online" if modality == "online" else "Presencial"
        description += f"\nModalidade: {modality_label}"
    if patient_number:
        number_clean = patient_number.replace("@s.whatsapp.net", "")
        description += f"\nNúmero: {number_clean}"
    if patient_email:
        description += f"\nE-mail: {patient_email}"
    if is_minor_first:
        description += "\n\n1ª hora: conversa com os pais/responsáveis\n2ª hora: consulta com o paciente"

    new_summary = f"Consulta — {patient_name}"
    if modality:
        modality_label = "Online" if modality == "online" else "Presencial"
        new_summary += f" [{modality_label}]"

    patch = {
        "summary": new_summary,
        "start": {"dateTime": new_start.isoformat(), "timeZone": TIMEZONE},
        "end":   {"dateTime": new_end.isoformat(),   "timeZone": TIMEZONE},
        "description": description,
    }
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _update_event, service, calendar_id, event_id, patch)
