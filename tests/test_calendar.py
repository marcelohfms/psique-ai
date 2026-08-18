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


@pytest.fixture(autouse=True)
def _no_real_supabase():
    """Nenhum teste deste arquivo pode falar com o Supabase de verdade.

    O cruzamento com `appointments` roda em TODA chamada de get_available_slots, então
    sem esse guard os testes que só mockam o Google passariam a abrir cliente e a
    consultar o banco de produção. Os testes do cruzamento sobrescrevem este patch."""
    with patch("app.database.get_supabase", new_callable=AsyncMock,
               return_value=_make_supabase([])):
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


# ── "próxima quarta" logo após já ter mostrado essa quarta ─────────────────────
# Regression Clara (558197140676, 2026-08-18, terça): a Eva ofertou quarta 19/08,
# a paciente respondeu "Próxima quarta" e a Eva repetiu os MESMOS slots de 19/08.
# Ao pé da letra "próxima quarta" numa terça É a quarta de amanhã — por isso o
# fix é de prompt (instruir a Eva a passar "quarta da próxima semana" para
# avançar), não da tool. Estes testes fixam o contraste que motiva a regra.

def test_parse_day_proxima_quarta_is_literal_this_week(freeze_calendar_tuesday):
    """'próxima quarta' numa terça = a quarta de amanhã (mesma semana) — a
    armadilha: se a Eva repassar essa expressão à tool, volta a mesma data."""
    from app.google_calendar import _parse_day
    assert _parse_day("próxima quarta") == date(2026, 7, 8)


def test_parse_day_quarta_proxima_semana_advances(freeze_calendar_tuesday):
    """Para avançar de fato, a Eva deve passar 'quarta da próxima semana'."""
    from app.google_calendar import _parse_day
    assert _parse_day("quarta da próxima semana") == date(2026, 7, 15)


def test_prompt_guides_advancing_when_same_weekday_already_offered():
    """Ambos os blocos de prompt (paciente existente e novo) devem instruir a
    avançar uma semana quando o paciente pede 'próxima [dia]' logo após já ter
    visto esse mesmo dia — nunca repetir a lista já enviada."""
    from app.graph.prompts import EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM
    for block in (EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM):
        assert "próxima quarta" in block
        assert "da próxima semana" in block
        assert "NUNCA repita a lista de horários" in block


# ── mês sozinho não é um dia ───────────────────────────────────────────────────
# Regression Elisabete/Isaac (5581987385089, 2026-08-02): a paciente perguntou
# "quais os dias disponíveis nesse mês?" e a LLM chamou a tool com
# preferred_day="setembro". _parse_day devolvia silenciosamente 01/09 — uma
# terça, dia em que o Dr. Júlio não atende — e a Eva respondeu sobre uma data
# que ninguém pediu. Um mês é um intervalo: _parse_day agora rejeita, e quem
# quer varrer o mês usa is_month_only()/month_and_year().

class _FrozenDTJulySaturday(_real_dt):
    """'Today' = 2026-07-11, a Saturday, mid-July."""
    @classmethod
    def now(cls, tz=None):
        return _real_dt(2026, 7, 11, 10, 0, tzinfo=tz) if tz else _real_dt(2026, 7, 11, 10, 0)


@pytest.fixture
def freeze_calendar_mid_july():
    with patch("app.google_calendar.datetime", _FrozenDTJulySaturday):
        yield


def test_parse_day_month_name_alone_is_rejected(freeze_calendar_tuesday):
    """'setembro' é um mês, não um dia → None (antes devolvia 01/09)."""
    from app.google_calendar import _parse_day
    assert _parse_day("setembro") is None
    assert _parse_day("agosto") is None
    assert _parse_day("mês de outubro") is None


def test_is_month_only_recognizes_whole_month_expressions(freeze_calendar_tuesday):
    from app.google_calendar import is_month_only
    assert is_month_only("setembro")
    assert is_month_only("mês de setembro")
    assert is_month_only("final de agosto")
    assert is_month_only("setembro de 2027")


def test_is_month_only_false_when_text_points_to_a_specific_day(freeze_calendar_tuesday):
    from app.google_calendar import is_month_only
    assert not is_month_only("15 de setembro")
    assert not is_month_only("quinta de agosto")
    assert not is_month_only("15/09")
    assert not is_month_only("próxima semana")


def test_month_and_year_current_and_future_months(freeze_calendar_mid_july):
    """Mês corrente ou futuro fica no ano corrente (hoje = 11/07/2026)."""
    from app.google_calendar import month_and_year
    assert month_and_year("julho") == (2026, 7)
    assert month_and_year("setembro") == (2026, 9)


def test_month_and_year_past_month_rolls_to_next_year(freeze_calendar_tuesday):
    from app.google_calendar import month_and_year
    assert month_and_year("janeiro") == (2027, 1)


def test_month_and_year_explicit_year_wins(freeze_calendar_tuesday):
    from app.google_calendar import month_and_year
    assert month_and_year("setembro de 2027") == (2027, 9)


def test_month_and_year_none_without_month(freeze_calendar_tuesday):
    from app.google_calendar import month_and_year
    assert month_and_year("segunda-feira") is None


# ── dia + nome do mês ──────────────────────────────────────────────────────────
# "15 de setembro" também caía no fallback de mês (01/09). Agora é uma data.

def test_parse_day_day_number_plus_month_name(freeze_calendar_tuesday):
    from app.google_calendar import _parse_day
    assert _parse_day("15 de setembro") == date(2026, 9, 15)
    assert _parse_day("dia 3 de outubro") == date(2026, 10, 3)


def test_parse_day_day_number_plus_past_month_rolls_to_next_year(freeze_calendar_tuesday):
    """Hoje = 07/07/2026; '2 de janeiro' só pode ser 2027."""
    from app.google_calendar import _parse_day
    assert _parse_day("2 de janeiro") == date(2027, 1, 2)


def test_parse_day_day_number_plus_month_with_explicit_year(freeze_calendar_tuesday):
    from app.google_calendar import _parse_day
    assert _parse_day("15 de setembro de 2027") == date(2027, 9, 15)


def test_parse_day_invalid_day_for_month_returns_none(freeze_calendar_tuesday):
    from app.google_calendar import _parse_day
    assert _parse_day("31 de setembro") is None


# ── "final de <mês>" = última semana do mês ────────────────────────────────────
# Regression Dione/Pedro Lins (5581999578203, 2026-07-30): a responsável pediu
# "final de agosto" e a Eva ofereceu 06/08, 10/08 e 13/08. O qualificador
# "final" era ignorado e a busca caía no início do mês. "Final" de um mês
# significa a ÚLTIMA SEMANA (últimos 7 dias corridos) daquele mês.

def test_month_end_window_start_is_last_seven_days(freeze_calendar_tuesday):
    """Agosto 2026 tem 31 dias → janela 25–31; setembro tem 30 → 24–30."""
    from app.google_calendar import _month_end_window_start
    assert _month_end_window_start(2026, 8) == date(2026, 8, 25)
    assert _month_end_window_start(2026, 9) == date(2026, 9, 24)


def test_wants_month_end_recognizes_qualifiers():
    from app.google_calendar import _wants_month_end
    assert _wants_month_end("final de agosto")
    assert _wants_month_end("fim de setembro")
    assert _wants_month_end("última semana de agosto")
    assert not _wants_month_end("agosto")


def test_parse_day_final_de_mes_is_month_only(freeze_calendar_tuesday):
    """'final de agosto' também é um intervalo, não um dia: quem responde é a
    varredura de mês (_search_month_shift), que respeita a última semana."""
    from app.google_calendar import _parse_day, is_month_only
    assert is_month_only("final de agosto")
    assert _parse_day("final de agosto") is None


def test_parse_day_weekday_in_month_end_still_returns_a_day(freeze_calendar_tuesday):
    """Com dia da semana o pedido vira uma data: 'quinta do final de agosto'
    → 1ª quinta dentro da janela 25–31/08 = 27/08."""
    from app.google_calendar import _parse_day
    assert _parse_day("quinta do final de agosto") == date(2026, 8, 27)


def test_parse_day_fim_de_semana_is_not_month_end(freeze_calendar_tuesday):
    """'fim de semana' (= sábado/domingo) não é qualificador de fim de mês e
    segue sem data reconhecida."""
    from app.google_calendar import _parse_day
    assert _parse_day("fim de semana") is None


# ── merge_adjacent_windows ─────────────────────────────────────────────────────

def test_merge_adjacent_windows_joins_contiguous_same_modality():
    """Júlio's Thursday grade: 14–18 and 18–20 have no gap between them and
    share modality "escolha" — must merge into a single 14–20 window."""
    from app.google_calendar import merge_adjacent_windows
    result = merge_adjacent_windows([(9, 0, 12, 0, "escolha"), (14, 0, 18, 0, "escolha"), (18, 0, 20, 0, "escolha")])
    assert result == [(9, 0, 12, 0, "escolha"), (14, 0, 20, 0, "escolha")]


def test_merge_adjacent_windows_keeps_gap_separate():
    """A real gap (9-12 then 14-18) must stay as two windows."""
    from app.google_calendar import merge_adjacent_windows
    result = merge_adjacent_windows([(9, 0, 12, 0, "escolha"), (14, 0, 18, 0, "escolha")])
    assert result == [(9, 0, 12, 0, "escolha"), (14, 0, 18, 0, "escolha")]


def test_merge_adjacent_windows_keeps_modality_change_separate():
    """Contiguous windows with different modality must not merge — an online-only
    slot right after a "escolha" one is a real boundary, not a data-modeling split."""
    from app.google_calendar import merge_adjacent_windows
    result = merge_adjacent_windows([(14, 0, 18, 0, "escolha"), (18, 0, 20, 0, "online")])
    assert result == [(14, 0, 18, 0, "escolha"), (18, 0, 20, 0, "online")]


def test_merge_adjacent_windows_empty_list():
    from app.google_calendar import merge_adjacent_windows
    assert merge_adjacent_windows([]) == []


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


async def test_bruna_monday_default_shift_includes_half_hour_window(freeze_calendar_now):
    """Dra. Bruna's Monday window is 07:30-08:30. With no specific shift
    requested ("qualquer"), every window for the day must be offered as-is,
    including this one that starts on a half hour.
    Regression: the shift-overlap filter used to compare whole hours only
    (entry[2] > shift_start_h -> 8 > 8 -> False), incorrectly discarding this
    window under the "qualquer" default shift bounds (8-18h) before it could
    even be clipped."""
    from app.google_calendar import get_available_slots

    service = _make_service([])  # empty calendar
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # Monday
            preferred_shift="qualquer",
            slot_minutes=60,
            doctor_key="bruna",
        )
    assert (datetime(2026, 3, 23, 7, 30, tzinfo=TZ), "online") in slots


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


# ── format_doctor_days_summary ─────────────────────────────────────────────────
# Caso Elisabete/Isaac (5581987385089, 02/08/2026): na mesma conversa a Eva disse
# "Dr. Júlio atende segunda, quarta e quinta" (correto) e, horas depois, "segundas,
# quartas ou sextas, que são os dias de atendimento do Dr. Júlio" (errado — sexta é
# da Dra. Bruna). A grade de dias precisa chegar ao prompt derivada de
# DOCTOR_SCHEDULES, em uma frase fechada e explícita, em vez de ser inferida.

def test_days_summary_julio_lists_only_his_real_weekdays():
    from app.google_calendar import format_doctor_days_summary

    julio_line = next(
        l for l in format_doctor_days_summary().splitlines() if "Dr. Júlio" in l
    )
    assert "segunda" in julio_line
    assert "quarta" in julio_line
    assert "quinta" in julio_line
    assert "sexta" not in julio_line, f"sexta é dia da Dra. Bruna: {julio_line!r}"
    assert "terça" not in julio_line


def test_days_summary_bruna_lists_only_her_real_weekdays():
    from app.google_calendar import format_doctor_days_summary

    bruna_line = next(
        l for l in format_doctor_days_summary().splitlines() if "Dra. Bruna" in l
    )
    assert "segunda" in bruna_line
    assert "quarta" in bruna_line
    assert "sexta" in bruna_line
    assert "quinta" not in bruna_line


def test_days_summary_marks_shifts_per_weekday():
    """Quarta do Dr. Júlio é só manhã — a súmula não pode generalizar 'manhã e
    tarde' para todos os dias, como a Eva fez às 07:59."""
    from app.google_calendar import format_doctor_days_summary

    julio_line = next(
        l for l in format_doctor_days_summary().splitlines() if "Dr. Júlio" in l
    )
    assert "quarta (só manhã)" in julio_line, julio_line
    assert "segunda (manhã e tarde)" in julio_line, julio_line
    assert "quinta (manhã, tarde e noite)" in julio_line, julio_line


def test_days_summary_is_derived_from_doctor_schedules():
    """Mudar DOCTOR_SCHEDULES muda a súmula — nada de texto fixo."""
    from app.google_calendar import format_doctor_days_summary

    fake = {"julio": {1: [(14, 0, 18, 0, "escolha")]}}
    with patch("app.google_calendar.DOCTOR_SCHEDULES", fake):
        text = format_doctor_days_summary()

    assert "Dra. Bruna" not in text
    julio_line = next(l for l in text.splitlines() if "Dr. Júlio" in l)
    assert "terça (só tarde)" in julio_line, julio_line
    assert "segunda" not in julio_line


# ── format_doctor_days_summary: exceções de data ──────────────────────────────
# A súmula de dias é a grade semanal permanente. Sem as exceções junto, no
# cadastro (COLLECT_SYSTEM, que não recebe format_doctor_schedules) a Eva diz
# "Dr. Júlio atende segunda" num feriado.

def test_days_summary_lists_upcoming_blocked_dates():
    from app.google_calendar import format_doctor_days_summary

    with patch("app.google_calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 2)
        mock_date.fromisoformat = date.fromisoformat
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        text = format_doctor_days_summary()

    assert "DATAS EXCEPCIONAIS" in text
    exc_lines = text.split("DATAS EXCEPCIONAIS")[1]
    bruna_exc = next(l for l in exc_lines.splitlines() if "Dra. Bruna" in l)
    assert "03/08 (segunda) sem atendimento" in bruna_exc, bruna_exc
    assert "05/08 (quarta) sem atendimento" in bruna_exc, bruna_exc
    assert "07/08 (sexta) sem atendimento" in bruna_exc, bruna_exc
    # Dr. Júlio tem 03/08 com a tarde reduzida (17h bloqueado)
    julio_exc = next(l for l in exc_lines.splitlines() if "Dr. Júlio" in l)
    assert "03/08 (segunda) atende manhã e tarde" in julio_exc, julio_exc


def test_days_summary_lists_reduced_schedule_exception():
    """31/08 é segunda, mas o Dr. Júlio só atende de manhã nessa data."""
    from app.google_calendar import format_doctor_days_summary

    with patch("app.google_calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 25)
        mock_date.fromisoformat = date.fromisoformat
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        text = format_doctor_days_summary()

    julio_exc = next(
        l for l in text.split("DATAS EXCEPCIONAIS")[1].splitlines() if "Dr. Júlio" in l
    )
    assert "31/08 (segunda) atende só manhã" in julio_exc, julio_exc


def test_days_summary_omits_exception_block_when_none_upcoming():
    from app.google_calendar import format_doctor_days_summary

    with patch("app.google_calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 9, 15)
        mock_date.fromisoformat = date.fromisoformat
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        text = format_doctor_days_summary()

    assert "DATAS EXCEPCIONAIS" not in text
    assert "Dr. Júlio atende SOMENTE" in text


# ── cruzamento com appointments do Supabase ───────────────────────────────────
#
# Caso Maria Clara Ramos Perecmanis (03/08/2026 17h, Dr. Júlio, taxa paga em
# 18/05): os eventos foram deletados do Calendar durante um ajuste de agenda em
# 28-29/07, mas a linha em `appointments` seguiu `scheduled`. Como a
# disponibilidade saía só do Calendar, em 30/07 a Eva ofereceu e vendeu o mesmo
# 17h para outra paciente — as duas pagaram.


def _make_supabase(rows: list[dict]) -> MagicMock:
    """Cliente Supabase mockado que devolve `rows` para a query de appointments."""
    from tests.conftest import make_supabase_client
    client, _table, execute = make_supabase_client()
    execute.return_value = MagicMock(data=rows)
    return client


def _appt_row(start_iso: str, end_iso: str, appointment_id: str = "evt-123") -> dict:
    return {"appointment_id": appointment_id, "start_time": start_iso, "end_time": end_iso}


async def test_slots_blocked_by_supabase_row_without_calendar_event(freeze_calendar_now):
    """Consulta `scheduled` sem evento no Calendar (slot fantasma) não pode ser oferecida."""
    from app.google_calendar import get_available_slots

    service = _make_service([])  # Calendar vazio — o evento foi deletado
    supabase = _make_supabase([
        _appt_row("2026-03-23T10:00:00-03:00", "2026-03-23T11:00:00-03:00"),
    ])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=service), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=supabase):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # segunda, Dr. Júlio atende 9-12
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )

    hours = [dt.hour for dt, _ in slots]
    assert 10 not in hours, f"slot fantasma das 10h foi oferecido: {hours}"
    assert hours == [9, 11]


async def test_supabase_crosscheck_queries_only_scheduled(freeze_calendar_now):
    """`pending_reschedule` não pode bloquear: nesse estado o slot é liberado de
    propósito enquanto o paciente escolhe o novo horário. O filtro é `eq`, não `in_`."""
    from app.google_calendar import get_available_slots

    supabase = _make_supabase([])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=supabase):
        await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )

    query = supabase.from_.return_value
    status_filters = [c for c in query.eq.call_args_list if c.args[0] == "status"]
    assert status_filters, "a query não filtrou por status"
    assert all(c.args[1] == "scheduled" for c in status_filters)
    query.in_.assert_not_called()


async def test_supabase_row_without_calendar_event_logs_divergence(freeze_calendar_now, caplog):
    """A inconsistência em si é um bug que alguém precisa ver — bloquear em silêncio
    esconderia o problema até virar duas pacientes pagando o mesmo horário."""
    import logging
    from app.google_calendar import get_available_slots

    supabase = _make_supabase([
        _appt_row("2026-03-23T10:00:00-03:00", "2026-03-23T11:00:00-03:00", "appt-fantasma"),
    ])
    with caplog.at_level(logging.ERROR, logger="app.google_calendar"), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=supabase):
        await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )

    assert "CALENDAR_DIVERGENCE" in caplog.text
    assert "appt-fantasma" in caplog.text


async def test_no_divergence_logged_when_calendar_covers_the_row(freeze_calendar_now, caplog):
    """O caso normal — linha no Supabase E evento no Calendar — não pode poluir o log
    de erro, senão o alerta de divergência vira ruído e ninguém olha."""
    import logging
    from app.google_calendar import get_available_slots

    busy = [{"start": "2026-03-23T10:00:00-03:00", "end": "2026-03-23T11:00:00-03:00"}]
    supabase = _make_supabase([
        _appt_row("2026-03-23T10:00:00-03:00", "2026-03-23T11:00:00-03:00"),
    ])
    with caplog.at_level(logging.ERROR, logger="app.google_calendar"), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service(busy)), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=supabase):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )

    assert "CALENDAR_DIVERGENCE" not in caplog.text
    assert [dt.hour for dt, _ in slots] == [9, 11]


async def test_supabase_failure_keeps_calendar_slots(freeze_calendar_now):
    """Fail-open: derrubar toda a oferta de horários por uma falha de banco é pior
    que o risco residual do slot fantasma."""
    from app.google_calendar import get_available_slots

    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])), \
         patch("app.database.get_supabase", new_callable=AsyncMock,
               side_effect=RuntimeError("supabase fora do ar")):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
            doctor_key="julio",
        )

    assert [dt.hour for dt, _ in slots] == [9, 10, 11]


async def test_no_supabase_crosscheck_without_doctor_key(freeze_calendar_now):
    """Sem doctor_key não há como identificar o médico — pula a checagem em vez de
    bloquear com dados de outra agenda."""
    from app.google_calendar import get_available_slots

    supabase = _make_supabase([])
    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])), \
         patch("app.database.get_supabase", new_callable=AsyncMock, return_value=supabase):
        await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="manha",
            slot_minutes=60,
        )

    supabase.from_.assert_not_called()


# ── slot que atravessa a borda do turno ───────────────────────────────────────
# A segunda da Dra. Bruna vai das 16:30 às 18:30, mas "tarde" termina às 18:00 e
# "noite" começa às 18:00. O recorte cortava o FIM da janela, então o 17:30
# (17:30→18:30) estourava a tarde e não cabia na noite (18:00→18:30 é curto
# demais): o horário sumia dos dois turnos e não era ofertado a ninguém.
# Vale para qualquer janela que cruze 12:00, 13:00 ou 18:00.


async def test_slot_que_termina_depois_do_turno_ainda_e_ofertado(freeze_calendar_now):
    """Bruna, segunda 16:30-18:30, tarde: 16:30 E 17:30 — o 17:30 termina 18:30."""
    from app.google_calendar import get_available_slots

    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # segunda
            preferred_shift="tarde",
            slot_minutes=60,
            doctor_key="bruna",
        )

    assert [dt.strftime("%H:%M") for dt, _ in slots] == ["16:30", "17:30"]


async def test_slot_que_atravessa_a_borda_nao_aparece_tambem_no_turno_seguinte(freeze_calendar_now):
    """O 17:30 é da tarde (é onde ele COMEÇA). A noite não pode repeti-lo, senão o
    mesmo horário aparece duas vezes para a paciente."""
    from app.google_calendar import get_available_slots

    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",
            preferred_shift="noite",
            slot_minutes=60,
            doctor_key="bruna",
        )

    assert slots == []


async def test_slot_na_borda_das_18h_pertence_a_noite_e_nao_duplica(freeze_calendar_now):
    """Dr. Júlio na quinta atende 14:00-18:00 e 18:00-20:00. O 18:00 começa na noite,
    então é da noite — e a tarde, que termina às 18:00, não pode ofertá-lo também."""
    from app.google_calendar import get_available_slots

    async def _slots(shift):
        with patch("app.google_calendar._credentials", return_value=MagicMock()), \
             patch("app.google_calendar.build", return_value=_make_service([])):
            got = await get_available_slots(
                calendar_id="cal-test",
                preferred_day="2026-03-26",  # quinta
                preferred_shift=shift,
                slot_minutes=60,
                doctor_key="julio",
            )
        return [dt.strftime("%H:%M") for dt, _ in got]

    tarde = await _slots("tarde")
    noite = await _slots("noite")

    assert "18:00" in noite
    assert "18:00" not in tarde
    assert not (set(tarde) & set(noite)), f"horário duplicado entre turnos: {set(tarde) & set(noite)}"


async def test_slot_de_2h_que_atravessa_a_borda_do_turno(freeze_calendar_now):
    """A consulta de 2h da primeira consulta de menor também atravessa a borda:
    na segunda da Bruna, 16:30→18:30 cabe na janela e não pode sumir só porque a
    tarde termina às 18:00."""
    from app.google_calendar import get_available_slots

    with patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("app.google_calendar.build", return_value=_make_service([])):
        slots = await get_available_slots(
            calendar_id="cal-test",
            preferred_day="2026-03-23",  # Bruna: segunda 16:30-18:30
            preferred_shift="tarde",
            slot_minutes=120,
            doctor_key="bruna",
        )

    horas = [dt.strftime("%H:%M") for dt, _ in slots]
    assert horas == ["16:30"], f"16:30→18:30 cabe inteiro na janela: {horas}"
