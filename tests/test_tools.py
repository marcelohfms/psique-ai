"""Tests for each tool in app/graph/tools.py."""
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage

from tests.conftest import PHONE, CONFIG

TZ = ZoneInfo("America/Recife")


def _make_state(**kwargs) -> dict:
    base = {
        "phone": PHONE,
        "stage": "patient_agent",
        "user_name": "Maria",
        "patient_name": "Maria",
        "patient_age": 30,
        "is_patient": True,
        "preferred_doctor": "julio",
        "guardian_relationship": None,
        "messages": [],
        "modality_restriction": None,
    }
    base.update(kwargs)
    return base


def _make_supabase_client():
    execute = AsyncMock(return_value=MagicMock(data=[]))
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert", "or_", "filter", "is_"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


# ── _sanitize_social_name ─────────────────────────────────────────────────────

def test_sanitize_social_name_strips_age_suffix_with_comma():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("Malu, 25 anos") == "Malu"


def test_sanitize_social_name_strips_age_suffix_without_comma():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("Malu 8 anos") == "Malu"


def test_sanitize_social_name_strips_parenthetical():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("Malu (é como minha família me chama)") == "Malu"


def test_sanitize_social_name_keeps_clean_name_untouched():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("  João Gabriel  ") == "João Gabriel"


def test_sanitize_social_name_empty_after_stripping_returns_empty():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("(  )") == ""


def test_sanitize_social_name_handles_none():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name(None) == ""


def test_sanitize_social_name_handles_empty_string():
    from app.graph.tools import _sanitize_social_name
    assert _sanitize_social_name("") == ""


# ── get_available_slots ───────────────────────────────────────────────────────

async def test_get_available_slots_returns_formatted_list():
    from app.graph.tools import get_available_slots
    slots = [
        (datetime(2026, 3, 23, 9, 0, tzinfo=TZ), "escolha"),
        (datetime(2026, 3, 23, 10, 0, tzinfo=TZ), "online"),
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=slots):
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "09:00" in result
    assert "10:00" in result


async def test_get_available_slots_no_slots_returns_message():
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]):
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "Não encontrei horários disponíveis para segunda-feira" in result


async def test_get_available_slots_logs_slots_offered_when_slots_returned():
    """Rastreio de 'pediu data e não continuou': quando horários reais são
    apresentados ao paciente, emite o evento slots_offered (fire-and-forget)
    que alimenta a auditoria de agendamento abandonado."""
    from app.graph.tools import get_available_slots
    slots = [
        (datetime(2026, 3, 23, 9, 0, tzinfo=TZ), "escolha"),
        (datetime(2026, 3, 23, 10, 0, tzinfo=TZ), "online"),
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=slots), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    mock_log.assert_awaited_once()
    args, _ = mock_log.call_args
    assert args[0] == "slots_offered"
    assert args[1] == PHONE
    assert args[2]["doctor"] == "julio"
    assert args[2]["preferred_day"] == "segunda"
    assert args[2]["preferred_shift"] == "manha"
    assert args[2]["slot_duration_minutes"] == 60


async def test_get_available_slots_no_slots_does_not_log_offer():
    """Sem horários (mensagem de indisponibilidade, sem HH:MM), NÃO emite
    slots_offered — não houve oferta que o paciente pudesse aceitar."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    mock_log.assert_not_awaited()


async def test_get_available_slots_restriction_message_does_not_log_offer():
    """Mensagem de restrição de médico (ex.: Dra. Bruna < 12 anos) não contém
    HH:MM e não deve emitir slots_offered."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", patient_age=8),
            config=CONFIG,
        )
    assert "Dra. Bruna atende apenas" in result
    mock_log.assert_not_awaited()


async def test_get_available_slots_urgent_same_day_calls_transfer_to_human_directly():
    """Regression: the model previously had to remember to call transfer_to_human
    after seeing "AGENDAMENTO_URGENTE", and sometimes it only told the patient it
    would transfer without actually invoking the tool (leaving the conversation
    stuck with the bot still active and nobody notified). get_available_slots
    must now trigger the real handoff itself instead of relying on the model."""
    from app.graph.tools import get_available_slots

    fixed_now = datetime(2026, 7, 31, 13, 0, tzinfo=TZ)
    weekday = fixed_now.weekday()
    schedules = {"julio": {weekday: [(9, 0, 18, 0, "escolha")]}}

    mock_dt = MagicMock(wraps=datetime)
    mock_dt.now.return_value = fixed_now

    with patch("app.graph.tools.datetime", mock_dt), \
         patch("app.google_calendar._parse_day", return_value=fixed_now.date()), \
         patch("app.google_calendar.DOCTOR_SCHEDULES", schedules), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.tools.transfer_to_human.coroutine", new_callable=AsyncMock,
               return_value="👤 Vou transferir você para um de nossos atendentes. Um momento, por favor!") as mock_transfer:
        result = await get_available_slots.coroutine(
            preferred_day="hoje",
            preferred_shift="tarde",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    mock_transfer.assert_awaited_once()
    _, kwargs = mock_transfer.call_args
    assert kwargs["state"] == _make_state()
    assert kwargs["config"] == CONFIG
    assert "hoje" in kwargs["reason"].lower() or "urgente" in kwargs["reason"].lower()
    assert "Vou transferir você para um de nossos atendentes" in result


async def test_get_available_slots_bruna_always_60min():
    """Dra. Bruna overrides slot_duration_minutes to 60 regardless of input."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]) as mock_slots:
        await get_available_slots.coroutine(
            preferred_day="quarta",
            preferred_shift="manha",
            slot_duration_minutes=120,
            state=_make_state(preferred_doctor="bruna"),
            config=CONFIG,
        )
    _, kwargs = mock_slots.call_args
    assert kwargs.get("slot_minutes") == 60 or mock_slots.call_args[0][3] == 60


async def test_get_available_slots_bruna_rejects_patient_under_12():
    """Dra. Bruna must not attend patients younger than 12."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="quarta",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", patient_age=8),
            config=CONFIG,
        )
    assert "12 anos" in result
    mock_slots.assert_not_called()


async def test_get_available_slots_bruna_age_exception_bypasses_under_12():
    """age_exception=True deve permitir paciente menor de 12 anos com Dra. Bruna."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]):
        result = await get_available_slots.coroutine(
            preferred_day="quarta",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", patient_age=8, age_exception=True),
            config=CONFIG,
        )
    assert "12 anos" not in result


async def test_get_available_slots_julio_rejects_patient_over_65():
    """Dr. Júlio não deve atender pacientes acima de 65 anos."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="julio", patient_age=70),
            config=CONFIG,
        )
    assert "65 anos" in result
    mock_slots.assert_not_called()


async def test_get_available_slots_julio_age_exception_bypasses_over_65():
    """age_exception=True deve permitir paciente acima de 65 anos com Dr. Júlio."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]):
        result = await get_available_slots.coroutine(
            preferred_day="segunda",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="julio", patient_age=70, age_exception=True),
            config=CONFIG,
        )
    assert "65 anos" not in result


_real_dt = datetime


class _FrozenDTTuesday(_real_dt):
    """'Today' = 2026-07-07, uma terça-feira, com 4 dias úteis restantes nesta
    semana (terça a sexta) e a semana seguinte começando em 13/07 (segunda)."""
    @classmethod
    def now(cls, tz=None):
        return _real_dt(2026, 7, 7, 10, 0, tzinfo=tz) if tz else _real_dt(2026, 7, 7, 10, 0)


# ── get_available_slots — "qualquer dia" (sem preferência de dia) ─────────────

async def test_get_available_slots_qualquer_dia_uses_current_week_when_enough_days():
    """'qualquer dia' com >=2 dias distintos disponíveis nesta semana NÃO deve buscar a semana seguinte."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-07", "2026-07-08"):
            day = int(preferred_day[-2:])
            return [(datetime(2026, 7, day, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "07/07" in result
    assert "08/07" in result
    assert "semana seguinte" not in result.lower()
    assert "outras semanas" not in result.lower()
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert "2026-07-13" not in called_days  # nunca buscou a semana seguinte


async def test_get_available_slots_qualquer_dia_extends_to_next_week_when_few():
    """Menos de 2 dias distintos nesta semana → soma a semana seguinte inteira."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift != "manha":
            return []
        if preferred_day == "2026-07-07":  # só terça nesta semana
            return [(datetime(2026, 7, 7, 9, 0, tzinfo=TZ), "escolha")]
        if preferred_day == "2026-07-13":  # segunda da semana seguinte
            return [(datetime(2026, 7, 13, 9, 0, tzinfo=TZ), "escolha")]
        if preferred_day == "2026-07-15":  # quarta da semana seguinte
            return [(datetime(2026, 7, 15, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "07/07" in result
    assert "13/07" in result
    assert "15/07" in result
    assert "outras semanas" in result.lower()


# ── _search_week — varredura de uma semana específica ─────────────────────────

async def test_search_week_next_week_lists_all_days_with_slots():
    """_search_week(1) sob _FrozenDTTuesday deve varrer 13–17/07 (seg–sex da
    semana seguinte) e listar todos os dias com vaga, sem teto de 3 dias."""
    from app.graph.tools import _search_week

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-13", "2026-07-15", "2026-07-17"):
            d = int(preferred_day[-2:])
            return [(datetime(2026, 7, d, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await _search_week(
            week_offset=1,
            calendar_id="cal123",
            doctor="julio",
            preferred_shift="manha",
            slot_duration_minutes=60,
        )

    assert "13/07" in result
    assert "15/07" in result
    assert "17/07" in result
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert "2026-07-07" not in called_days   # nunca tocou a semana atual
    assert "2026-07-20" not in called_days   # nunca passou da semana seguinte


async def test_search_week_this_week_only_remaining_business_days():
    """_search_week(0) sob _FrozenDTTuesday varre só ter–sex (07–10/07),
    nunca segunda (06/07, já passou)."""
    from app.graph.tools import _search_week

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-08":
            return [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await _search_week(
            week_offset=0,
            calendar_id="cal123",
            doctor="julio",
            preferred_shift="manha",
            slot_duration_minutes=60,
        )

    assert "08/07" in result
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert "2026-07-06" not in called_days   # segunda já passou


async def test_search_week_falls_back_to_any_day_when_target_week_empty():
    """Semana alvo vazia → delega a _search_any_day (nunca 'não encontrei')."""
    from app.graph.tools import _search_week

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        # nada na semana seguinte (13–17), só na semana atual (08/07)
        if preferred_shift == "manha" and preferred_day == "2026-07-08":
            return [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await _search_week(
            week_offset=1,
            calendar_id="cal123",
            doctor="julio",
            preferred_shift="manha",
            slot_duration_minutes=60,
        )

    assert "08/07" in result   # veio do fallback _search_any_day


# ── get_available_slots — expressão de semana → relação (não pergunta o dia) ──

async def test_get_available_slots_proxima_semana_lists_next_week():
    """'próxima semana' → lista dias da semana seguinte, sem CLARIFICAÇÃO."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-13", "2026-07-15"):
            d = int(preferred_day[-2:])
            return [(datetime(2026, 7, d, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="próxima semana",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "13/07" in result
    assert "15/07" in result


async def test_get_available_slots_proxima_semana_works_with_qualquer_shift():
    """Regressão da armadilha: 'próxima semana' + shift 'qualquer' NÃO pode cair
    em 'Não entendi a data' — o roteamento vem antes do branch de shift."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_day == "2026-07-13" and preferred_shift == "tarde":
            return [(datetime(2026, 7, 13, 14, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="próxima semana",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "Não entendi a data" not in result
    assert "13/07" in result


async def test_get_available_slots_essa_semana_lists_remaining_days():
    """'essa semana' → dias úteis restantes desta semana (ter–sex)."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-09":
            return [(datetime(2026, 7, 9, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="essa semana",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "09/07" in result


async def test_get_available_slots_em_breve_uses_any_day():
    """'em breve' (vago genérico) → próximos dias com vaga, sem CLARIFICAÇÃO."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-07", "2026-07-08"):
            d = int(preferred_day[-2:])
            return [(datetime(2026, 7, d, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="em breve",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "07/07" in result


# ── get_available_slots — "final de <mês>" (última semana do mês) ─────────────
# Regression Dione/Pedro Lins (5581999578203, 2026-07-30): a responsável pediu
# "final de agosto" com turno "tarde" e a Eva ofereceu 06/08, 10/08 e 13/08 —
# _search_month_shift ignorava o qualificador "final" e devolvia os 3 primeiros
# dias do mês com vaga. "Final" de um mês = última semana (últimos 7 dias).

async def test_get_available_slots_final_de_agosto_only_offers_last_week():
    """'final de agosto' + tarde → só dias da última semana de agosto (25–31)."""
    from datetime import date as _date
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        if preferred_shift == "tarde" and d.month == 8:
            return [(datetime(2026, 8, d.day, 14, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="final de agosto",
            preferred_shift="tarde",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    # Nunca consultou dias fora da última semana de agosto (31 dias → 25–31)
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert called_days, "esperava chamadas ao calendário"
    assert all(_date.fromisoformat(d).day >= 25 for d in called_days), called_days
    # E só ofereceu dias da última semana
    assert "06/08" not in result
    assert "10/08" not in result
    assert "13/08" not in result
    assert "25/08" in result


async def test_get_available_slots_ultima_semana_de_agosto_routes_to_month_scan():
    """'última semana de agosto' contém "semana", mas deve continuar no month
    scan (25–31) — o catch-all de "semana" NÃO pode sequestrar a frase por causa
    do substring, senão ofereceria qualquer dia da semana atual."""
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        if preferred_shift == "tarde" and d.month == 8:
            return [(datetime(2026, 8, d.day, 14, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="última semana de agosto",
            preferred_shift="tarde",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    # Todas as consultas ao calendário caíram na última semana de agosto (25–31)
    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert called_days, "esperava chamadas ao calendário"
    assert all(
        _date(2026, 8, 25) <= _date.fromisoformat(d) <= _date(2026, 8, 31)
        for d in called_days
    ), called_days
    # E ofereceu dias da última semana (month scan rodou, catch-all não sequestrou)
    assert "25/08" in result or "27/08" in result


async def test_get_available_slots_final_de_agosto_no_slots_says_final_do_mes():
    """Sem vagas na última semana → mensagem fala do FINAL do mês, sem oferecer
    dias do início/meio como fallback silencioso."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="final de agosto",
            preferred_shift="tarde",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "final de agosto" in result.lower()


async def test_get_available_slots_mes_sem_qualificador_ainda_busca_do_inicio():
    """'agosto' sem qualificador segue devolvendo os primeiros dias do mês."""
    from datetime import date as _date
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        if preferred_shift == "tarde" and d.month == 8:
            return [(datetime(2026, 8, d.day, 14, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="agosto",
            preferred_shift="tarde",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "03/08" in result  # 1º dia útil de agosto/2026


# ── get_available_slots — mês inteiro sem turno definido ─────────────────────
# Regression Elisabete/Isaac (5581987385089, 2026-08-02): a paciente perguntou
# "quais os dias disponíveis nesse mês?", a LLM chamou a tool com
# preferred_day="setembro" + preferred_shift="qualquer", e o ramo de turno
# "qualquer" mandava o mês para _parse_day, que devolvia 01/09 — uma terça, dia
# em que o Dr. Júlio não atende. Resposta: "não há horários para terça-feira,
# dia 01/09", sobre uma data que ninguém pediu.

class _FrozenDTAugustSunday(_real_dt):
    """'Today' = 2026-08-02, domingo — mesma data do caso real."""
    @classmethod
    def now(cls, tz=None):
        return _real_dt(2026, 8, 2, 14, 44, tzinfo=tz) if tz else _real_dt(2026, 8, 2, 14, 44)


async def test_get_available_slots_mes_sem_turno_lista_dias_do_mes():
    """'setembro' + turno 'qualquer' → varre o mês e lista os DIAS com vaga,
    em vez de responder sobre o dia 1º."""
    from datetime import date as _date
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        if d.weekday() == 1:  # terça: Dr. Júlio não atende
            return []
        return [(datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ), "escolha")]

    with patch("app.graph.tools.datetime", _FrozenDTAugustSunday), \
         patch("app.google_calendar.datetime", _FrozenDTAugustSunday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="setembro",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    # Varreu vários dias de setembro, não uma data só
    called_days = [_date.fromisoformat(c.kwargs["preferred_day"]) for c in mock_slots.call_args_list]
    assert len(called_days) > 1
    assert all(d.month == 9 and d.year == 2026 for d in called_days), called_days
    # Nunca afirma indisponibilidade nem fala do dia 1º (terça sem atendimento)
    assert "Não há horários" not in result
    assert "01/09" not in result
    assert "02/09" in result  # quarta, 1º dia útil com vaga
    assert "Setembro" in result


async def test_get_available_slots_mes_sem_turno_sem_vaga_fala_do_mes():
    """Mês sem nenhuma vaga → a mensagem cita o MÊS, nunca um dia específico."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        return []

    with patch("app.graph.tools.datetime", _FrozenDTAugustSunday), \
         patch("app.google_calendar.datetime", _FrozenDTAugustSunday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="setembro",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "Setembro" in result
    assert "01/09" not in result
    assert "terça" not in result.lower()


async def test_get_available_slots_dia_com_nome_do_mes_vai_para_a_data():
    """'15 de setembro' é uma data, não um mês: consulta só esse dia."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        return [(datetime(2026, 9, 15, 9, 0, tzinfo=TZ), "escolha")]

    with patch("app.graph.tools.datetime", _FrozenDTAugustSunday), \
         patch("app.google_calendar.datetime", _FrozenDTAugustSunday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="15 de setembro",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    called_days = {c.kwargs["preferred_day"] for c in mock_slots.call_args_list}
    assert called_days == {"15 de setembro"}
    assert "15/09" in result


async def test_get_available_slots_data_ininteligivel_pede_clarificacao():
    """Sem data reconhecível ('esse mês'), pede clarificação em vez de afirmar
    indisponibilidade de um dia que ninguém pediu."""
    from app.graph.tools import get_available_slots

    with patch("app.graph.tools.datetime", _FrozenDTAugustSunday), \
         patch("app.google_calendar.datetime", _FrozenDTAugustSunday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, return_value=[]) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="esse mês",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO NECESSÁRIA" in result
    mock_slots.assert_not_called()


async def test_get_available_slots_qualquer_dia_de_setembro_busca_o_mes():
    """'qualquer dia de setembro' é sobre setembro, não sobre a semana atual."""
    from datetime import date as _date
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        return [(datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ), "escolha")]

    with patch("app.graph.tools.datetime", _FrozenDTAugustSunday), \
         patch("app.google_calendar.datetime", _FrozenDTAugustSunday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia de setembro",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    called_days = [_date.fromisoformat(c.kwargs["preferred_day"]) for c in mock_slots.call_args_list]
    assert all(d.month == 9 for d in called_days), called_days
    assert "01/09" in result  # 1º dia útil de setembro/2026 (terça, aqui com vaga fake)


async def test_get_available_slots_qualquer_dia_keeps_expanding_until_found():
    """Duas semanas totalmente vazias NUNCA devem gerar mensagem de 'não encontrei' —
    a busca deve continuar expandindo até achar algo."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-20":  # 3ª semana, segunda
            return [(datetime(2026, 7, 20, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "20/07" in result
    assert "não encontrei" not in result.lower()


async def test_get_available_slots_qualquer_dia_continues_past_blocked_week_when_still_few_days():
    """1 dia disponível na semana atual + semana seguinte totalmente bloqueada (ex: recesso
    do médico) não pode fazer a busca parar aí — precisa continuar expandindo até achar pelo
    menos _ANY_DAY_MIN_DISTINCT_DAYS dias distintos, mesmo já tendo achado algo antes."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift != "manha":
            return []
        if preferred_day == "2026-07-07":  # só terça nesta semana
            return [(datetime(2026, 7, 7, 9, 0, tzinfo=TZ), "escolha")]
        # semana seguinte (13-17/07) totalmente bloqueada (recesso) — nenhum slot
        if preferred_day == "2026-07-20":  # segunda da 3ª semana
            return [(datetime(2026, 7, 20, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "07/07" in result
    assert "20/07" in result


async def test_get_available_slots_qualquer_dia_e_qualquer_turno_shows_per_shift_breakdown():
    """'qualquer dia' combinado com turno 'qualquer' (o caso real mais comum, já
    que a Eva pergunta o dia antes do turno) deve mostrar o detalhamento por turno."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_day == "2026-07-07" and preferred_shift == "tarde":
            return [(datetime(2026, 7, 7, 14, 0, tzinfo=TZ), "escolha")]
        if preferred_day == "2026-07-08" and preferred_shift == "manha":
            return [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "Tarde: 14:00" in result
    assert "Manhã: 09:00" in result


async def test_get_available_slots_turno_qualquer_inclui_modalidade_por_horario():
    """Caso real (5587996089614, 04/08/2026): dia específico + turno "qualquer"
    devolvia só "Manhã: 09:00, 10:00 / Noite: 18:00", sem nenhuma etiqueta de
    modalidade. Sem essa informação a Eva inventou que o horário era
    "exclusivamente online". Todo horário listado precisa vir etiquetado."""
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        if preferred_shift == "manha":
            return [(datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ), "escolha")]
        if preferred_shift == "noite":
            return [(datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ), "online")]
        return []

    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="quinta",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "09:00 [online ou presencial — paciente escolhe livremente]" in result
    assert "18:00 [apenas online]" in result


async def test_get_available_slots_turno_qualquer_fallback_1h_inclui_modalidade():
    """Mesmo no fallback de 1h (quando não há bloco de 2h seguidas), os horários
    precisam vir com a modalidade — senão a Eva volta a adivinhar."""
    from datetime import date as _date
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        d = _date.fromisoformat(preferred_day)
        if slot_minutes == 60 and preferred_shift == "manha":
            return [(datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="quinta",
            preferred_shift="qualquer",
            slot_duration_minutes=120,
            state=_make_state(),
            config=CONFIG,
        )

    assert "09:00 [online ou presencial — paciente escolhe livremente]" in result


async def test_pick_doctor_by_earliest_availability_picks_earlier_doctor():
    """'qualquer um' entre dois médicos válidos → escolhe o de agenda mais próxima.
    Bruna tem vaga na terça (07/07); Júlio só na quinta (09/07) → retorna 'bruna'."""
    from app.graph.tools import pick_doctor_by_earliest_availability

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if doctor_key == "bruna" and preferred_day == "2026-07-07" and preferred_shift == "manha":
            return [(datetime(2026, 7, 7, 9, 0, tzinfo=TZ), "escolha")]
        if doctor_key == "julio" and preferred_day == "2026-07-09" and preferred_shift == "manha":
            return [(datetime(2026, 7, 9, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        doctor = await pick_doctor_by_earliest_availability(["julio", "bruna"], slot_duration_minutes=60)

    assert doctor == "bruna"


async def test_pick_doctor_by_earliest_availability_bruna_uses_60min():
    """Bruna sempre usa slots de 60min, mesmo quando o parâmetro pede 120 (menor 1ª
    consulta). Só Júlio deve ser consultado com 120min."""
    from app.graph.tools import pick_doctor_by_earliest_availability

    seen: list[tuple[str, int]] = []

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        seen.append((doctor_key, slot_minutes))
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        await pick_doctor_by_earliest_availability(["julio", "bruna"], slot_duration_minutes=120)

    bruna_durations = {mins for doc, mins in seen if doc == "bruna"}
    julio_durations = {mins for doc, mins in seen if doc == "julio"}
    assert bruna_durations == {60}
    assert julio_durations == {120}


async def test_get_available_slots_semana_que_vem_lists_next_week():
    """'semana que vem' agora lista a semana seguinte em vez de pedir clarificação
    (comportamento novo — antes devolvia CLARIFICAÇÃO NECESSÁRIA)."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day == "2026-07-14":
            return [(datetime(2026, 7, 14, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._prefetch_supabase_busy", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock, side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="semana que vem",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "CLARIFICAÇÃO" not in result
    assert "14/07" in result


# ── confirm_appointment ───────────────────────────────────────────────────────

async def test_confirm_appointment_creates_event_and_notifies():
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-abc123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "evt-abc123" in result
    assert "Dr. Júlio" in result
    mock_notify.assert_called()  # clinic notified


async def test_confirm_appointment_insert_uses_patient_id_and_contact_id():
    """O insert de novo agendamento grava patient_id + contact_id (não user_id)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _user = {"id": "p-1", "_contact_id": "c-1"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-pid"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_user]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_user), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    _insert_payload = table.insert.call_args[0][0]
    assert _insert_payload.get("patient_id") == "p-1"
    assert _insert_payload.get("contact_id") == "c-1"
    assert "user_id" not in _insert_payload


async def test_confirm_appointment_multi_patient_empty_override_asks_for_name():
    """Contato com múltiplos pacientes e SEM patient_name_override: em vez de gravar no
    escuro pelo user_db_id/patient_name congelados (que causou o caso Renata/Laila+Suzi,
    5581996962165, 14/08/2026 — consulta pedida para Laila nasceu sob Suzi), a tool pede o
    nome completo e NÃO cria evento nem insere agendamento."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    create_event = AsyncMock(return_value="evt-should-not-happen")
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", create_event), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Laila Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
        )
    assert "NÃO ENVIE AO PACIENTE" in result
    assert "nome completo" in result.lower()
    assert not table.insert.called
    assert not create_event.called


async def test_confirm_appointment_multi_patient_nonunique_override_asks_for_name():
    """Override que não casa com exatamente um paciente (typo 'Layla', nome parcial, ou dois
    irmãos parecidos) NÃO pode cair no fallback user_db_id — _match_patient_by_name devolve
    None e a rede de segurança pede o nome, sem gravar. Trava o risco principal do design."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    create_event = AsyncMock(return_value="evt-should-not-happen")
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", create_event), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Suzi Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Layla",
        )
    assert "NÃO ENVIE AO PACIENTE" in result
    assert not table.insert.called
    assert not create_event.called


async def test_confirm_appointment_multi_patient_valid_override_with_session_note_inserts():
    """A 2ª sessão da 1ª consulta de menor dividida chama confirm_appointment de novo para o
    MESMO paciente, com session_note. Num contato multi-paciente, desde que o override do menor
    seja passado, a rede de segurança NÃO deve travar — o agendamento é inserido normalmente."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-split-2"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Suzi Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
            session_note="2ª hora — paciente",
            patient_name_override="Laila Monteiro Viana",
        )
    assert "AGENDAMENTO_OK" in result
    assert "nome completo" not in result.lower()
    assert table.insert.called
    _insert_payload = table.insert.call_args[0][0]
    assert _insert_payload.get("patient_id") == "laila-id"


async def test_confirm_appointment_multi_patient_override_beats_user_db_id():
    """patient_name_override (uso da atendente) deve poder mirar num paciente diferente
    do que está em user_db_id — é o mecanismo explícito para isso, documentado na tool."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _laila = {"id": "laila-id", "patient_name": "Laila Monteiro Viana", "name": "Renata Monteiro"}
    _suzi = {"id": "suzi-id", "patient_name": "Suzi Monteiro Viana", "name": "Renata Monteiro"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-override"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_laila, _suzi]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="suzi-id", patient_name="Suzi Monteiro Viana", patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Laila Monteiro Viana",
        )
    _insert_payload = table.insert.call_args[0][0]
    assert _insert_payload.get("patient_id") == "laila-id"


async def test_guard_blocks_second_appointment_when_user_db_id_is_orphan():
    """A guarda de "paciente já tem consulta" resolve o paciente pelo TELEFONE, não por
    state["user_db_id"] — que congela quando a conversa sai do collect_info e passou a
    apontar para ids inexistentes após a migração users→patients (101 de 491 threads em
    03/08/2026).

    Caso Dione / Pedro Lins De Araújo (5581999578203, 30/07/2026): o state carregava
    user_db_id=67b1673f (fantasma), a guarda consultou appointments por esse id, não achou
    nada e liberou um SEGUNDO agendamento ativo — enquanto o insert, que resolvia por
    telefone, gravou o patient_id correto. O agendamento antigo ficou órfão, recebeu
    cobrança de taxa e foi auto-cancelado, disparando "sua vaga foi liberada" para uma
    paciente que estava em dia."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _pedro = {"id": "pedro-id", "patient_name": "Pedro Lins De Araújo", "_contact_id": "c-dione"}
    # Consulta futura JÁ existente do Pedro (outro horário) — a guarda tem de vê-la.
    execute.return_value = MagicMock(data=[
        {"appointment_id": "appt-existente", "start_time": "2027-01-15T14:00:00+00:00", "status": "scheduled"},
    ])
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_pedro]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_pedro), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="67b1673f-fantasma", patient_name="Pedro Lins De Araújo"),
            config=CONFIG,
        )
    # A guarda resolveu pelo telefone e consultou o patient_id REAL, não o fantasma.
    _eq_args = [c.args for c in table.eq.call_args_list if c.args and c.args[0] == "patient_id"]
    assert ("patient_id", "pedro-id") in _eq_args
    assert ("patient_id", "67b1673f-fantasma") not in _eq_args
    # E bloqueou: nenhum evento criado, instrução interna de remarcar devolvida.
    assert "já tem consulta" in result
    mock_create.assert_not_called()


async def test_guard_does_not_block_sibling_on_shared_phone():
    """Contraprova da guarda acima: 23 contatos têm mais de um paciente (famílias que
    usam um telefone só). Resolver pelo telefone NÃO pode bloquear o agendamento de um
    irmão porque outro tem consulta marcada — a guarda precisa mirar exatamente o
    paciente desta conversa (caso Daniela/Silvia/Flavia Passos, 5581981179458).

    Com a política de override obrigatório para contato multi-paciente, o nome do
    irmão-alvo vai em patient_name_override; a asserção segue sendo que o guard mira
    exatamente esse paciente."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _silvia = {"id": "silvia-id", "patient_name": "Silvia De Souza Passos", "name": "Daniela Passos"}
    _flavia = {"id": "flavia-id", "patient_name": "Flavia Souza Passos", "name": "Daniela Passos"}
    _daniela = {"id": "daniela-id", "patient_name": "Daniela De Souza Passos", "name": "Daniela Passos"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-flavia") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[_silvia, _flavia, _daniela]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_silvia), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="flavia-id", patient_name="Flavia Souza Passos"),
            config=CONFIG,
            patient_name_override="Flavia Souza Passos",
        )
    # Consultou a agenda da Flavia — não a da Silvia (que get_user_by_phone devolveria).
    _eq_args = [c.args for c in table.eq.call_args_list if c.args and c.args[0] == "patient_id"]
    assert ("patient_id", "flavia-id") in _eq_args
    assert ("patient_id", "silvia-id") not in _eq_args
    assert "já tem consulta" not in result
    mock_create.assert_called()
    assert table.insert.call_args[0][0].get("patient_id") == "flavia-id"


async def test_resolve_patient_for_booking_ignores_orphan_id_for_single_patient_contact():
    """Contato com um único paciente (1351 de 1374): a resolução é puramente pelo
    telefone, então um user_db_id órfão no state não tem como influenciar."""
    from app.graph.tools import _resolve_patient_for_booking
    _pedro = {"id": "pedro-id", "patient_name": "Pedro Lins De Araújo"}
    with patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_pedro]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_pedro):
        user = await _resolve_patient_for_booking(
            "5581999578203", {"user_db_id": "67b1673f-fantasma", "patient_name": "Pedro Lins De Araújo"},
        )
    assert user["id"] == "pedro-id"


async def test_confirm_appointment_matches_sibling_by_social_name():
    """Duas pacientes no mesmo telefone (irmãs); uma tem social_name. Um override
    usando o nome social deve resolver para a paciente certa, não para a outra
    nem falhar o match (caso análogo a Laila/Suzi Viana, mas com nome social)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _joao = {"id": "joao-id", "patient_name": "João Pedro Viana", "name": "Renata Viana", "social_name": None}
    _maria = {"id": "maria-id", "patient_name": "Maria Eduarda Viana", "name": "Renata Viana", "social_name": "Malu"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-alias"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_joao, _maria]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_joao), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_name="Zé", patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Malu",
        )
    _insert_payload = table.insert.call_args[0][0]
    assert _insert_payload.get("patient_id") == "maria-id"


async def test_confirm_appointment_normalizes_attendant_all_caps_name():
    """Nota da atendente com o nome do paciente em CAIXA ALTA não deve vazar assim
    para o evento do Calendar nem para a notificação da clínica — ambos devem usar
    o nome canônico de patients.name (caso João Pedro Lins Da Costa Gomes / Ednara
    de Morais Lins, 5581992349207, 2026-07-27)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _joao = {"id": "joao-id", "patient_name": "João Pedro Lins Da Costa Gomes", "name": "Ednara de Morais Lins"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-caps") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_joao]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_joao), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_name="JOÃO PEDRO LINS DA COSTA GOMES"),
            config=CONFIG,
            patient_name_override="JOÃO PEDRO LINS DA COSTA GOMES",
        )
    assert mock_create.call_args.kwargs["patient_name"] == "João Pedro Lins Da Costa Gomes"
    _notify_msg = mock_notify.call_args[0][0]
    assert "Paciente: João Pedro Lins Da Costa Gomes" in _notify_msg
    assert mock_notify.call_args.kwargs["subject"] == "Agendamento realizado — João Pedro Lins Da Costa Gomes"


async def test_confirm_appointment_shows_social_name_in_calendar_and_email():
    """Quando o paciente tem social_name registrado, o evento do Calendar e o
    e-mail da clínica mostram 'Nome Civil (Nome Social)' — nome civil primeiro
    (casa com CPF/prontuário), nome social entre parênteses (como chamar o
    paciente)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _patient = {"id": "patient-id", "patient_name": "Maria Eduarda Viana", "name": "Renata Viana", "social_name": "Malu"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-social") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_name="Maria Eduarda Viana"),
            config=CONFIG,
        )
    assert mock_create.call_args.kwargs["patient_name"] == "Maria Eduarda Viana (Malu)"
    _notify_msg = mock_notify.call_args[0][0]
    assert "Paciente: Maria Eduarda Viana (Malu)" in _notify_msg
    assert mock_notify.call_args.kwargs["subject"] == "Agendamento realizado — Maria Eduarda Viana (Malu)"


async def test_confirm_appointment_no_social_name_uses_plain_name():
    """Regressão: sem social_name, Calendar e e-mail continuam mostrando só o
    nome civil (comportamento existente, sem parênteses vazios)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _patient = {"id": "patient-id", "patient_name": "Carlos Silva", "name": "Carlos Silva"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-plain") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_name="Carlos Silva"),
            config=CONFIG,
        )
    assert mock_create.call_args.kwargs["patient_name"] == "Carlos Silva"
    assert "Paciente: Carlos Silva\n" in mock_notify.call_args[0][0]


async def test_confirm_appointment_resolves_social_name_alias_with_multiple_candidates():
    """Quando há mais de um paciente no telefone (irmãs) e o override bate só com
    o social_name de uma delas, a resolução de nome canônico (usada pro Calendar
    e e-mail) precisa achar essa paciente pelo social_name — não só a resolução
    de patient_id (Task 8) — senão o Calendar mostraria 'Malu' cru, sem o nome
    civil, justamente no caso em que o médico mais precisa dele."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _joao = {"id": "joao-id", "patient_name": "João Pedro Viana", "name": "Renata Viana", "social_name": None}
    _maria = {"id": "maria-id", "patient_name": "Maria Eduarda Viana", "name": "Renata Viana", "social_name": "Malu"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-canon-alias") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_joao, _maria]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(patient_email="renata@example.com"),
            config=CONFIG,
            patient_name_override="Malu",
        )
    assert mock_create.call_args.kwargs["patient_name"] == "Maria Eduarda Viana (Malu)"
    assert "Paciente: Maria Eduarda Viana (Malu)" in mock_notify.call_args[0][0]


async def test_confirm_appointment_with_session_note():
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-xyz"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
            session_note="1ª hora — responsáveis",
        )
    assert "1ª hora — responsáveis" in result


async def test_confirm_appointment_rolls_back_calendar_on_db_failure():
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    execute.side_effect = Exception("DB error")
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rollback"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "erro" in result.lower()
    mock_cancel.assert_awaited_once_with("cal123", "evt-rollback")


async def test_confirm_appointment_rolls_back_calendar_on_patient_resolution_failure():
    """Falha ANTES do insert (ex.: get_users_by_phone explode por instabilidade do
    Supabase) também deve desfazer o evento do Calendar. Antes dessa correção, o
    try/except só envolvia o insert final — uma exceção em get_users_by_phone/
    _match_by_name deixava o evento órfão no Calendar sem nenhuma linha em
    appointments, e sem essa linha o guard de duplicata nunca disparava, então cada
    retry criava um evento novo (caso Silvia De Souza Passos, 5581998483157: 5
    eventos órfãos no calendário do Dr. Júlio em 10/07/2026 para o dia 24/07 11h)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-orphan"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, side_effect=Exception("Supabase down")), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "erro" in result.lower()
    mock_cancel.assert_awaited_once_with("cal123", "evt-orphan")
    table.insert.assert_not_called()


async def test_confirm_appointment_respects_online_modality_restriction():
    """Se modality_restriction="online" no state, confirm_appointment ignora o modality arg."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rest-online") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="online"),
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — should be overridden
        )
    assert "evt-rest-online" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "online"


async def test_confirm_appointment_respects_presencial_modality_restriction():
    """Se modality_restriction="presencial" no state, confirm_appointment usa presencial."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-rest-pres") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),
            config=CONFIG,
            modality="online",  # LLM passed online — should be overridden
        )
    assert "evt-rest-pres" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "presencial"


async def test_confirm_appointment_no_restriction_uses_slot_logic():
    """Sem restrição cadastral, a lógica de slot é aplicada normalmente."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-no-rest") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction=None),
            config=CONFIG,
            modality="presencial",
        )
    assert "evt-no-rest" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "presencial"


async def test_confirm_appointment_blocks_when_patient_has_pending_reschedule():
    """Guard 0 deve bloquear confirm_appointment mesmo quando a consulta existente do
    paciente já está em pending_reschedule (não só 'scheduled') E mesmo quando a data
    original já passou. Caso contrário, confirm_appointment escapa da checagem e cria uma
    linha nova em vez de deixar reschedule_appointment atualizar a consulta existente —
    perdendo a taxa de reserva já paga (caso Tiago Perrelli, 03/07/2026; caso Heitor/
    Ludmilla, 5581996937559, 21/07/2026: pending_reschedule de 02/07 remarcado semanas
    depois). Guard filtra por patient_id específico, não por contato inteiro."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _past = (datetime.now(_tz.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    # Agora apenas 1 execute call: appointments.select().eq("patient_id", ...).in_("status", ...)
    execute.side_effect = [
        MagicMock(data=[{"appointment_id": "old-evt-1", "start_time": _past,
                          "status": "pending_reschedule"}]),   # appointments guard 0
    ]
    _patient = {"id": "patient-1", "patient_name": "Maria"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="patient-1"),
            config=CONFIG,
        )
    assert "NÃO crie um novo agendamento" in result
    assert "mark_reschedule_in_progress" in result
    mock_create.assert_not_called()
    _status_call = next(c for c in table.in_.call_args_list if c.args[0] == "status")
    assert set(_status_call.args[1]) == {"scheduled", "pending_reschedule"}


async def test_confirm_appointment_allows_when_only_past_scheduled_not_completed():
    """Um agendamento 'scheduled' com data no passado (consulta já atendida, ainda não
    marcada como completed) NÃO deve bloquear um novo agendamento — só pending_reschedule
    (qualquer data) e scheduled futuro travam o Guard 0. Sem essa distinção, um paciente
    com uma consulta antiga nunca conseguiria marcar de novo. O filtro de data precisa
    rodar em Python (não só no .gte da query) para ser testável e correto por status."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _past = (datetime.now(_tz.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _guard0 = MagicMock(data=[{"appointment_id": "old-done", "start_time": _past, "status": "scheduled"}])
    _calls = {"n": 0}

    def _side(*a, **k):
        _calls["n"] += 1
        return _guard0 if _calls["n"] == 1 else MagicMock(data=[])
    execute.side_effect = _side
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-new-ok"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "patient-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "patient-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="patient-1"),
            config=CONFIG,
        )
    assert "NÃO crie um novo agendamento" not in result
    assert "evt-new-ok" in result


async def test_confirm_appointment_guard0_applies_even_with_force_encaixe():
    """force_encaixe deve pular apenas os guards de janela/conflito de agenda, nunca o
    Guard 0 (paciente já tem consulta futura). Caso contrário, uma atendente pedindo para
    'encaixar' um novo horário faz a Eva criar um segundo agendamento em vez de remarcar
    o existente (caso Gustavo Lapenda, 06/07/2026 — dois agendamentos ativos)."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    # Guard 0 agora filtra por patient_id específico, apenas 1 execute call
    execute.side_effect = [
        MagicMock(data=[{"appointment_id": "old-evt-1", "start_time": _future,
                          "status": "scheduled"}]),   # appointments guard 0
    ]
    _patient = {"id": "patient-1", "patient_name": "Maria"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-07-08T13:20:00",
            slot_duration_minutes=60,
            state=_make_state(silent_mode=True, user_db_id="patient-1"),
            config=CONFIG,
            force_encaixe=True,
        )
    assert "NÃO crie um novo agendamento" in result
    assert "mark_reschedule_in_progress" in result
    mock_create.assert_not_called()


async def test_confirm_appointment_blocks_when_slot_taken_by_another_patient_in_supabase():
    """Guard 1b: o horário já está ocupado por OUTRO paciente do mesmo médico segundo o
    Supabase, mesmo que o evento não exista no Calendar. A Guard 1 só pega o próprio
    paciente e a Guard 2 só olha o Calendar — cega justamente à linha `scheduled` cujo
    evento sumiu (o slot-fantasma do caso Maria Clara: dois pacientes no mesmo 17h, os
    dois pagando). fetch_supabase_busy já impede a Eva de OFERECER esse slot; esta guarda
    é a rede de segurança no momento do confirm, para o slot que escapa mesmo assim."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    table.lt.return_value = table  # _make_supabase_client não encadeia lt/gt por padrão
    table.gt.return_value = table
    def _side(*a, **k):
        # A Guard 1b é a única query que encadeia .gt("end_time", ...) — devolve a
        # linha de OUTRO paciente só nela; as demais guardas ficam vazias.
        if table.gt.called:
            return MagicMock(data=[{"appointment_id": "evt-outro", "patient_id": "patient-OUTRO"}])
        return MagicMock(data=[])
    execute.side_effect = _side
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "patient-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "patient-1"}), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="patient-1"),
            config=CONFIG,
        )
    assert "já está ocupado" in result
    mock_create.assert_not_called()


async def test_confirm_appointment_slot_clash_guard_bypassed_by_force_encaixe():
    """force_encaixe pula a Guard 1b junto com as demais guardas de conflito de agenda:
    a atendente pediu explicitamente para encaixar sobre um horário ocupado."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    # Guard 1b nem deve rodar sob force_encaixe; se rodasse, esta linha bloquearia.
    execute.return_value = MagicMock(data=[{"appointment_id": "evt-outro", "patient_id": "patient-OUTRO"}])
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-encaixe"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "patient-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "patient-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-07-08T13:00:00",
            slot_duration_minutes=60,
            state=_make_state(silent_mode=True, user_db_id="patient-1"),
            config=CONFIG,
            force_encaixe=True,
        )
    assert "já está ocupado" not in result
    assert "evt-encaixe" in result



# ── confirm_appointment: 1ª consulta de menor dividida em duas sessões ─────────

def _split_state(**kwargs) -> dict:
    """State de menor agendando com o Dr. Júlio a 1ª consulta dividida em 2 sessões."""
    base = dict(patient_age=15, is_patient=False, preferred_doctor="julio",
                user_db_id="patient-1", patient_name="Marcelo Filho")
    base.update(kwargs)
    return _make_state(**base)


async def test_confirm_appointment_allows_second_split_session_of_minor_first():
    """A 1ª consulta de menor com o Dr. Júlio pode ser dividida em duas sessões de 1h em
    dias diferentes (1h com os responsáveis + 1h com o paciente) — a própria Eva oferece
    isso. Ao confirmar a 2ª sessão, o Guard 0 via "paciente já tem consulta futura" e
    mandava remarcar: a Eva chamava reschedule_appointment, o evento da 1ª sessão era
    apagado do Calendar e a MESMA linha era movida para a data da 2ª (caso Marcelo
    Rodrigues de Souza Brayner Filho, 5581999865181, 04/08/2026 — a consulta de 06/08
    09:00 com os responsáveis sumiu da agenda do Dr. Júlio).

    Com session_note preenchido e a consulta existente sendo a outra metade da mesma
    primeira consulta, o guard deve liberar o INSERT de uma segunda linha."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _guard0 = MagicMock(data=[{
        "appointment_id": "evt-1a-sessao", "start_time": _future, "status": "scheduled",
        "consultation_type": "primeira_consulta",
        "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False,
    }])
    _calls = {"n": 0}

    def _side(*a, **k):
        _calls["n"] += 1
        return _guard0 if _calls["n"] == 1 else MagicMock(data=[])
    execute.side_effect = _side
    _patient = {"id": "patient-1", "patient_name": "Marcelo Filho", "_contact_id": "c-1"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-2a-sessao") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_split_state(),
            config=CONFIG,
            session_note="2ª hora — paciente",
        )
    assert "NÃO crie um novo agendamento" not in result
    assert "evt-2a-sessao" in result
    mock_create.assert_called()
    assert table.insert.call_args[0][0].get("consultation_type") == "primeira_consulta"


async def test_second_split_session_inherits_booking_fee_from_first():
    """A taxa de reserva de R$ 100 é uma só para a primeira consulta inteira, paga na 1ª
    sessão. Se a linha da 2ª sessão entrasse com booking_fee_paid_at nulo,
    send_payment_reminders cobraria a taxa de novo e auto-cancelaria a sessão — logo a 2ª
    sessão herda o timestamp da 1ª."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _guard0 = MagicMock(data=[{
        "appointment_id": "evt-1a-sessao", "start_time": _future, "status": "scheduled",
        "consultation_type": "primeira_consulta",
        "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False,
    }])
    _calls = {"n": 0}

    def _side(*a, **k):
        _calls["n"] += 1
        return _guard0 if _calls["n"] == 1 else MagicMock(data=[])
    execute.side_effect = _side
    _patient = {"id": "patient-1", "patient_name": "Marcelo Filho", "_contact_id": "c-1"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-2a"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_split_state(),
            config=CONFIG,
            session_note="2ª hora — paciente",
        )
    assert table.insert.call_args[0][0].get("booking_fee_paid_at") == "2026-08-04T20:58:25+00:00"


async def test_split_session_exception_requires_session_note():
    """A exceção é estreita de propósito: sem session_note não há como distinguir a 2ª
    sessão de um agendamento duplicado comum, então o Guard 0 continua bloqueando."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    execute.side_effect = [MagicMock(data=[{
        "appointment_id": "evt-1a-sessao", "start_time": _future, "status": "scheduled",
        "consultation_type": "primeira_consulta",
        "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False,
    }])]
    _patient = {"id": "patient-1", "patient_name": "Marcelo Filho"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_split_state(),
            config=CONFIG,
        )
    assert "NÃO crie um novo agendamento" in result
    # ...mas a mensagem de bloqueio ensina o caminho certo, em vez de só mandar remarcar —
    # foi obedecendo ao "remarque" genérico que a Eva apagou a 1ª sessão do Marcelo.
    assert 'session_note="2ª hora — paciente"' in result
    mock_create.assert_not_called()


async def test_split_session_exception_does_not_apply_to_pending_reschedule():
    """pending_reschedule é remarcação de verdade em curso, com taxa já paga presa à
    linha. Mesmo com session_note, o guard tem de bloquear — senão a exceção reabre o
    buraco do caso Tiago Perrelli (linha nova em vez de update, taxa perdida)."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    execute.side_effect = [MagicMock(data=[{
        "appointment_id": "evt-1a-sessao", "start_time": _future, "status": "pending_reschedule",
        "consultation_type": "primeira_consulta",
        "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False,
    }])]
    _patient = {"id": "patient-1", "patient_name": "Marcelo Filho"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_split_state(),
            config=CONFIG,
            session_note="2ª hora — paciente",
        )
    assert "NÃO crie um novo agendamento" in result
    mock_create.assert_not_called()


async def test_split_session_exception_blocks_third_session():
    """A 1ª consulta dividida tem exatamente duas partes. Com duas já no banco, uma
    terceira com session_note é agendamento indevido e volta a ser bloqueada."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _f1 = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _f2 = (datetime.now(_tz.utc) + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _row = {"status": "scheduled", "consultation_type": "primeira_consulta",
            "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False}
    execute.side_effect = [MagicMock(data=[
        dict(_row, appointment_id="evt-1a", start_time=_f1),
        dict(_row, appointment_id="evt-2a", start_time=_f2),
    ])]
    _patient = {"id": "patient-1", "patient_name": "Marcelo Filho"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_split_state(),
            config=CONFIG,
            session_note="3ª hora — extra",
        )
    assert "NÃO crie um novo agendamento" in result
    mock_create.assert_not_called()


async def test_split_session_exception_does_not_apply_to_adult():
    """session_note não pode virar bypass genérico do Guard 0: paciente adulto com
    consulta futura continua bloqueado, independentemente da nota."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    execute.side_effect = [MagicMock(data=[{
        "appointment_id": "evt-adulto", "start_time": _future, "status": "scheduled",
        "consultation_type": "primeira_consulta",
        "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False,
    }])]
    _patient = {"id": "patient-1", "patient_name": "Maria"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(user_db_id="patient-1"),  # 30 anos, is_patient=True
            config=CONFIG,
            session_note="2ª hora — paciente",
        )
    assert "NÃO crie um novo agendamento" in result
    mock_create.assert_not_called()


async def test_split_session_exception_does_not_apply_to_acompanhamento():
    """A consulta existente ser 'acompanhamento' significa que não há primeira consulta
    dividida em curso — é um retorno já marcado, e um novo agendamento é remarcação."""
    from datetime import timezone as _tz
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    _future = (datetime.now(_tz.utc) + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    execute.side_effect = [MagicMock(data=[{
        "appointment_id": "evt-retorno", "start_time": _future, "status": "scheduled",
        "consultation_type": "acompanhamento",
        "booking_fee_paid_at": "2026-08-04T20:58:25+00:00", "booking_fee_waived": False,
    }])]
    _patient = {"id": "patient-1", "patient_name": "Marcelo Filho"}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_patient]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=_patient):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_split_state(),
            config=CONFIG,
            session_note="2ª hora — paciente",
        )
    assert "NÃO crie um novo agendamento" in result
    mock_create.assert_not_called()


# ── confirm_appointment: guard de duração do slot (Dr. Júlio) ──────────────────

async def test_confirm_appointment_julio_rejects_slot_that_overruns_window():
    """Dr. Júlio: bloco de 2h começando às 19:00 numa quinta (janela 18–20) termina
    21:00, estourando o fecho — deve ser rejeitado sem gravar (caso Bernardo, mãe
    Mônica, 5581991320003: 1ª consulta gravada 19:00–21:00 fora da grade)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-26T19:00:00",  # quinta-feira
            slot_duration_minutes=120,
            state=_make_state(preferred_doctor="julio"),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    mock_create.assert_not_called()


async def test_confirm_appointment_julio_accepts_2h_block_that_fits():
    """Dr. Júlio: bloco de 2h às 18:00 numa quinta cabe em 18–20 → aceito."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-2h-fit") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-26T18:00:00",  # quinta-feira
            slot_duration_minutes=120,
            state=_make_state(preferred_doctor="julio"),
            config=CONFIG,
        )
    assert "evt-2h-fit" in result
    mock_create.assert_called_once()


async def test_confirm_appointment_julio_accepts_2h_block_spanning_adjacent_windows():
    """Dr. Júlio: quinta-feira tem duas janelas contíguas na grade — 14–18 e 18–20 —
    que juntas formam um único expediente ininterrupto até as 20h. Um bloco de 2h
    começando às 17:00 (17–19) atravessa a fronteira das 18h entre essas duas
    tuplas, mas não estoura o expediente real — deve ser aceito (caso Davi Souza
    de Brito, 5581995011672, 06/08/2026: atendente pediu 17h–19h, sistema recusou
    dizendo que ultrapassava o expediente, embora o médico atenda até as 20h)."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-2h-span") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-26T17:00:00",  # quinta-feira
            slot_duration_minutes=120,
            state=_make_state(preferred_doctor="julio"),
            config=CONFIG,
        )
    assert "evt-2h-span" in result
    mock_create.assert_called_once()


async def test_confirm_appointment_julio_accepts_60min_split_at_19h():
    """Dr. Júlio: sessão separada de 1h às 19:00 numa quinta cabe em 18–20 → aceito."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-split-19") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-26T19:00:00",  # quinta-feira
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="julio"),
            config=CONFIG,
            session_note="1ª hora — responsáveis",
        )
    assert "evt-split-19" in result
    mock_create.assert_called_once()


# ── confirm_appointment: encaixe da Dra. Bruna começando a :20 vira 40min ──────

async def test_confirm_appointment_bruna_encaixe_at_20min_clamped_to_40():
    """Encaixe da Dra. Bruna começando a :20 termina no topo da hora (40min) para não
    bloquear o slot regular da hora seguinte (ex: sexta 13:20 → 14:00, mantém o 14h)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-enc-40") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-27T13:20:00",  # sexta-feira
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", silent_mode=True),
            config=CONFIG,
            force_encaixe=True,
        )
    assert mock_create.call_args.kwargs["slot_minutes"] == 40
    _insert_payload = table.insert.call_args[0][0]
    end_dt = datetime.fromisoformat(_insert_payload["end_time"])
    assert (end_dt.hour, end_dt.minute) == (14, 0)


async def test_confirm_appointment_bruna_encaixe_on_grid_stays_60():
    """Encaixe da Dra. Bruna on-grid (:00) não é encurtado — segue 60min."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-bruna"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-enc-60") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await confirm_appointment.coroutine(
            slot_datetime="2026-03-27T13:00:00",  # sexta-feira
            slot_duration_minutes=60,
            state=_make_state(preferred_doctor="bruna", silent_mode=True),
            config=CONFIG,
            force_encaixe=True,
        )
    assert mock_create.call_args.kwargs["slot_minutes"] == 60


# ── confirm_attendance (idempotência: primeiro a confirmar vence) ──────────────

async def test_confirm_attendance_marks_confirmed_when_not_yet_confirmed():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    # select de confirmed_at retorna vazio → ainda não confirmado
    execute.return_value = MagicMock(data=[{"confirmed_at": None}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "confirmada" in result.lower()
    table.update.assert_called()          # gravou confirmed_at
    mock_log.assert_awaited()             # logou o evento


async def test_confirm_attendance_is_idempotent_when_already_confirmed():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    # já existe confirmed_at → segunda confirmação é no-op (primeiro a confirmar vence)
    execute.return_value = MagicMock(data=[{"confirmed_at": "2026-06-19T10:00:00+00:00"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    table.update.assert_not_called()       # NÃO regravou confirmed_at
    mock_log.assert_not_awaited()          # NÃO logou de novo
    # A segunda confirmação NÃO pode devolver a mesma resposta da primeira: o
    # template de confirmação é verbatim no prompt, então repeti-lo produz uma
    # mensagem byte a byte idêntica à anterior (caso Dr. Paulo Diniz, 28/07/2026).
    # A tool precisa sinalizar ao LLM que já estava confirmada.
    assert "INSTRUÇÃO INTERNA" in result
    assert "NÃO repita" in result


async def test_confirm_attendance_rejects_unknown_appointment_id():
    """Caso Sayonara Lira (01/08/2026): sem consulta agendada, a Eva alucinou o
    appointment_id 'bruna-20260802T1100' (formato de SLOT LIVRE, não de consulta).
    O UPDATE não casou nenhuma linha e a tool ainda assim devolveu
    'Presença confirmada! ✅' — a Eva confirmou presença e mandou o endereço da
    clínica para uma consulta que não existe. A tool precisa recusar."""
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=[])   # nenhuma consulta com esse ID
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="bruna-20260802T1100",
            state=_make_state(),
            config=CONFIG,
        )
    table.update.assert_not_called()
    mock_log.assert_not_awaited()
    assert "INSTRUÇÃO INTERNA" in result
    assert "NÃO confirme presença" in result
    assert not result.startswith("Presença confirmada")


async def test_confirm_attendance_rejects_canceled_appointment():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=[{"confirmed_at": None, "status": "canceled"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-cancelada",
            state=_make_state(),
            config=CONFIG,
        )
    table.update.assert_not_called()
    mock_log.assert_not_awaited()
    assert "INSTRUÇÃO INTERNA" in result


async def test_confirm_attendance_rejects_completed_appointment():
    """Consulta já realizada (caso Sayonara: a única consulta dela, 17/06, está
    'completed'). Confirmar presença nela faria a Eva dizer 'te esperamos hoje'
    sobre uma consulta passada."""
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=[{"confirmed_at": None, "status": "completed"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-passada",
            state=_make_state(),
            config=CONFIG,
        )
    table.update.assert_not_called()
    mock_log.assert_not_awaited()
    assert "INSTRUÇÃO INTERNA" in result


async def test_confirm_attendance_accepts_pending_reschedule():
    """pending_reschedule ainda é uma consulta viva — não pode ser recusada."""
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=[{"confirmed_at": None, "status": "pending_reschedule"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await confirm_attendance.coroutine(
            appointment_id="evt-remarcar",
            state=_make_state(),
            config=CONFIG,
        )
    table.update.assert_called()
    mock_log.assert_awaited()
    assert "confirmada" in result.lower()


# ── cancel_appointment ────────────────────────────────────────────────────────

async def test_cancel_appointment_cancels_and_notifies():
    from app.graph.tools import cancel_appointment
    client, table, execute = _make_supabase_client()
    # maybe_single returns appointment data
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00"})
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await cancel_appointment.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "cancelada" in result.lower()
    assert "INSTRUÇÃO INTERNA" not in result  # paciente sem outras consultas ativas
    mock_cancel.assert_awaited_once_with("cal123", "evt-abc")
    mock_notify.assert_called()


async def test_cancel_appointment_warns_about_remaining_sibling():
    """Primeira consulta infantil = dois agendamentos que coexistem. Ao cancelar um,
    a tool deve avisar (instrução interna) que ainda há consulta ATIVA do mesmo
    paciente, para a Eva não reportar 'as consultas foram canceladas' tendo cancelado
    só uma. Regressão do caso Marcelo Brayner (5581999865181, 13/08/2026): Eva
    anunciou cancelar as duas partes (17/08 e 24/08) mas só chamou cancel_appointment
    na de 17/08 e mesmo assim confirmou ambas — a de 24/08 ficou scheduled."""
    from app.graph.tools import cancel_appointment
    client, table, execute = _make_supabase_client()
    execute.side_effect = [
        MagicMock(data={"start_time": "2026-08-17T14:00:00-03:00", "booking_fee_paid_at": None, "patient_id": "child-1"}),  # appt select
        MagicMock(data=[]),  # status update
        MagicMock(data=[  # outras consultas ativas do MESMO paciente (inclui a que acabou de cancelar)
            {"appointment_id": "evt-cancelada", "start_time": "2026-08-17T14:00:00-03:00"},
            {"appointment_id": "evt-sibling", "start_time": "2026-08-24T14:00:00-03:00"},
        ]),
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "child-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await cancel_appointment.coroutine(
            appointment_id="evt-cancelada",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "evt-sibling" in result          # aponta a consulta que ainda está ativa
    assert "evt-cancelada" not in result.split("INSTRUÇÃO INTERNA")[1]  # não lista a já cancelada
    assert "24/08/2026" in result


# ── cancel_all_appointments ──────────────────────────────────────────────────

async def test_cancel_all_appointments_cancels_every_active_one():
    """Cancela as duas partes da primeira consulta do MESMO paciente de uma vez.
    Deve liberar o Calendar de cada uma e marcar todas como canceled."""
    from app.graph.tools import cancel_all_appointments
    from app.database import DOCTOR_IDS
    client, table, execute = _make_supabase_client()
    julio = DOCTOR_IDS["julio"]
    execute.side_effect = [
        MagicMock(data={"patient_id": "child-1"}),  # resolve patient do appointment de referência
        MagicMock(data=[  # select das consultas ativas do paciente
            {"appointment_id": "evt-17", "start_time": "2026-08-17T14:00:00-03:00",
             "booking_fee_paid_at": "2026-08-04T17:57:00-03:00", "doctor_id": julio,
             "patient_id": "child-1", "status": "scheduled"},
            {"appointment_id": "evt-24", "start_time": "2026-08-24T14:00:00-03:00",
             "booking_fee_paid_at": None, "doctor_id": julio,
             "patient_id": "child-1", "status": "scheduled"},
        ]),
        MagicMock(data=[]),  # update evt-17
        MagicMock(data=[]),  # update evt-24
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[{"id": "child-1", "patient_name": "Marcelo Filho"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await cancel_all_appointments.coroutine(
            appointment_id="evt-17",
            state=_make_state(),
            config=CONFIG,
        )
    assert "2 consulta(s) cancelada(s)" in result
    assert "17/08/2026" in result and "24/08/2026" in result
    assert mock_cancel.await_count == 2  # libera o Calendar das duas
    mock_cancel.assert_any_await("cal-julio", "evt-17")
    mock_cancel.assert_any_await("cal-julio", "evt-24")
    assert mock_log.await_count == 2
    mock_notify.assert_called_once()  # uma única notificação de lote


async def test_cancel_all_appointments_rejects_appointment_of_other_contact():
    """appointment_id cujo patient_id não pertence a este contato → recusa, sem cancelar nada."""
    from app.graph.tools import cancel_all_appointments
    client, table, execute = _make_supabase_client()
    execute.side_effect = [MagicMock(data={"patient_id": "estranho-99"})]  # patient de outro contato
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[{"id": "child-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await cancel_all_appointments.coroutine(
            appointment_id="evt-de-outro",
            state=_make_state(),
            config=CONFIG,
        )
    assert "inválido" in result.lower()
    mock_cancel.assert_not_awaited()
    mock_notify.assert_not_called()


async def test_cancel_all_appointments_no_active_returns_message():
    """Sem consultas ativas: não chama Calendar nem notifica a clínica."""
    from app.graph.tools import cancel_all_appointments
    client, table, execute = _make_supabase_client()
    execute.side_effect = [
        MagicMock(data={"patient_id": "child-1"}),  # resolve patient
        MagicMock(data=[]),                          # select vazio
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock) as mock_cancel, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[{"id": "child-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await cancel_all_appointments.coroutine(
            appointment_id="evt-17",
            state=_make_state(),
            config=CONFIG,
        )
    assert "não há consultas ativas" in result.lower()
    mock_cancel.assert_not_awaited()
    mock_notify.assert_not_called()


async def test_cancel_all_appointments_preserve_fee_keeps_paid_ones():
    """preserve_fee=True: consultas com taxa paga viram pending_reschedule (FEE_PRESERVED)."""
    from app.graph.tools import cancel_all_appointments
    from app.database import DOCTOR_IDS
    client, table, execute = _make_supabase_client()
    julio = DOCTOR_IDS["julio"]
    execute.side_effect = [
        MagicMock(data={"patient_id": "child-1"}),  # resolve patient
        MagicMock(data=[
            {"appointment_id": "evt-17", "start_time": "2026-08-17T14:00:00-03:00",
             "booking_fee_paid_at": "2026-08-04T17:57:00-03:00", "doctor_id": julio,
             "patient_id": "child-1", "status": "scheduled"},
        ]),
        MagicMock(data=[]),
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-julio"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[{"id": "child-1", "patient_name": "Marcelo Filho"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await cancel_all_appointments.coroutine(
            appointment_id="evt-17",
            state=_make_state(),
            config=CONFIG,
            preserve_fee=True,
        )
    # a chamada de update deve ter marcado pending_reschedule
    update_call = [c for c in table.update.call_args_list]
    assert any(c.args and c.args[0].get("status") == "pending_reschedule" for c in update_call)
    assert "FEE_PRESERVED" in result


# ── mark_reschedule_in_progress ───────────────────────────────────────────────

async def test_mark_reschedule_in_progress_first_reschedule_notice():
    """Primeira remarcação dentro do prazo: marca em andamento e avisa que é única."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    future_start = (datetime.now(TZ) + timedelta(days=10)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": future_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.side_effect = [
        MagicMock(data=appt_data),  # appointment select
        MagicMock(count=0),         # reschedule count
        MagicMock(data=[]),         # cancel_event update
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "único reagendamento" in result.lower()
    assert "get_available_slots" in result


async def test_mark_reschedule_in_progress_canceled_status_says_slot_released():
    """Consulta já cancelada (ex: por timeout de taxa não paga): a tool não pode deixar
    a Eva inferir que a consulta ainda está reservada — regressão do caso Larissa
    (5581991947587, 2026-07-15), onde a Eva disse "ainda está reservada" para uma
    consulta que já tinha sido cancelada."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "canceled",
        "patient_id": "user-1",
        "start_time": (datetime.now(TZ) + timedelta(days=10)).isoformat(),
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]):
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "cancelada" in result.lower()
    assert "NÃO diga ao paciente que a consulta \"ainda está reservada\"" in result
    assert "get_available_slots" in result
    table.update.assert_not_called()


async def test_mark_reschedule_in_progress_completed_status_reports_real_status():
    """Status diferente de canceled (ex: completed) também não pode ser confundido
    com "ainda reservada/pendente" — a tool deve indicar o status real."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "completed",
        "patient_id": "user-1",
        "start_time": (datetime.now(TZ) - timedelta(days=10)).isoformat(),
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]):
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "completed" in result
    assert "NÃO afirme que a consulta ainda" in result
    table.update.assert_not_called()


async def test_mark_reschedule_in_progress_less_than_24h_blocks_free_flow():
    """Regra das 24h precede a regra do primeiro reagendamento: pedido de remarcação
    a menos de 24h da consulta (taxa já paga) deve redirecionar para o fluxo de nova
    cobrança, mesmo sendo a 1ª remarcação do paciente."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    near_start = (datetime.now(TZ) + timedelta(minutes=14)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": near_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "único reagendamento" not in result.lower()
    assert "cancel_appointment" in result
    assert "confirm_appointment" in result
    table.update.assert_not_called()
    mock_log.assert_not_awaited()


async def test_mark_reschedule_in_progress_less_than_24h_fee_unpaid_proceeds_normally():
    """Se a taxa ainda não foi paga, a remarcação segue o fluxo normal mesmo <24h."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    near_start = (datetime.now(TZ) + timedelta(minutes=14)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": near_start,
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
    }
    execute.side_effect = [
        MagicMock(data=appt_data),
        MagicMock(count=0),
        MagicMock(data=[]),
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" not in result
    assert "get_available_slots" in result


async def test_mark_reschedule_in_progress_silent_mode_bypasses_24h_guard():
    """Reagendamento iniciado pela atendente (silent_mode) ignora a checagem das 24h."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    near_start = (datetime.now(TZ) + timedelta(minutes=14)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": near_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.side_effect = [
        MagicMock(data=appt_data),
        MagicMock(count=0),
        MagicMock(data=[]),
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(silent_mode=True),
            config=CONFIG,
            initiated_by="clinic",
        )
    assert "INSTRUÇÃO INTERNA" not in result
    assert "get_available_slots" in result


async def test_mark_reschedule_in_progress_silent_mode_without_initiated_by_asks_clarification():
    """Nota da atendente sem deixar claro quem pediu a remarcação: Eva deve perguntar
    (em nota privada) antes de prosseguir, em vez de assumir um lado."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    future_start = (datetime.now(TZ) + timedelta(days=10)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": future_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(silent_mode=True),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "a pedido do paciente" in result.lower()
    assert "clínica" in result.lower()
    table.update.assert_not_called()
    mock_log.assert_not_awaited()


async def test_mark_reschedule_in_progress_silent_mode_persists_clinic_initiated():
    """Quando a atendente esclarece que a remarcação é por iniciativa da clínica,
    isso deve ser gravado no agendamento para não contar como remarcação do paciente."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    future_start = (datetime.now(TZ) + timedelta(days=10)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": future_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.side_effect = [
        MagicMock(data=appt_data),  # appointment select
        MagicMock(data=[]),         # cancel_event update
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(silent_mode=True),
            config=CONFIG,
            initiated_by="clinic",
        )
    assert "INSTRUÇÃO INTERNA" not in result
    assert "get_available_slots" in result
    update_call = table.update.call_args
    assert update_call[0][0].get("reschedule_initiated_by") == "clinic"


async def test_mark_reschedule_in_progress_non_silent_mode_always_persists_patient():
    """Fora do silent_mode, quem inicia é sempre o próprio paciente."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    future_start = (datetime.now(TZ) + timedelta(days=10)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": future_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.side_effect = [
        MagicMock(data=appt_data),  # appointment select
        MagicMock(count=0),         # reschedule count
        MagicMock(data=[]),         # cancel_event update
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    update_call = table.update.call_args
    assert update_call[0][0].get("reschedule_initiated_by") == "patient"


async def test_mark_reschedule_in_progress_count_query_excludes_clinic_initiated():
    """A contagem de remarcações do paciente não deve considerar reagendamentos
    marcados como iniciativa da clínica (senão o médico remarcar consome o
    benefício de remarcação grátis do paciente)."""
    from app.graph.tools import mark_reschedule_in_progress
    client, table, execute = _make_supabase_client()
    future_start = (datetime.now(TZ) + timedelta(days=10)).isoformat()
    appt_data = {
        "appointment_id": "evt-abc",
        "status": "scheduled",
        "patient_id": "user-1",
        "start_time": future_start,
        "booking_fee_paid_at": "2026-01-01T10:00:00-03:00",
        "booking_fee_waived": False,
    }
    execute.side_effect = [
        MagicMock(data=appt_data),
        MagicMock(count=0),
        MagicMock(data=[]),
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal-123"), \
         patch("app.google_calendar.cancel_event", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        await mark_reschedule_in_progress.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    table.or_.assert_called_once_with(
        "metadata->>initiated_by.is.null,metadata->>initiated_by.eq.patient"
    )


# ── reschedule_appointment ────────────────────────────────────────────────────

async def test_reschedule_appointment_updates_event_and_notifies():
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00", "patient_id": "user-1", "patients": {"name": "Maria"}})
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "remarcada" in result.lower()
    mock_update.assert_awaited_once()
    mock_notify.assert_called()


async def test_reschedule_appointment_blocks_when_new_slot_busy():
    """reschedule_appointment deve recusar gravar um novo horário que já está
    ocupado por outro agendamento no Calendar — sem isso, uma oferta desatualizada
    confirmada depois pode colidir com um horário que outro paciente já confirmou
    nesse meio-tempo (caso Raynner/Bernardo, 23/07/2026 19h com o Dr. Júlio).
    confirm_appointment já tinha esse busy-check; reschedule_appointment não tinha."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-03-20T09:00:00-03:00",
        "patient_id": "user-1",
        "patients": {"name": "Maria"},
    })
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[
             {"start": "2026-03-25T10:00:00-03:00", "end": "2026-03-25T11:00:00-03:00"}
         ]):
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "ocupado" in result.lower()
    mock_update.assert_not_awaited()
    mock_create.assert_not_awaited()


async def test_reschedule_appointment_resets_reminder_fields():
    """Reagendar deve zerar reminder_day_before_sent_at e reminder_day_of_sent_at."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={"start_time": "2026-03-23T09:00:00+00:00", "patient_id": "user-1", "patients": {"name": "Maria"}})
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    update_call = table.update.call_args
    assert update_call is not None
    update_data = update_call[0][0]
    assert update_data.get("reminder_day_before_sent_at") is None
    assert update_data.get("reminder_day_of_sent_at") is None
    assert "reminder_day_before_sent_at" in update_data
    assert "reminder_day_of_sent_at" in update_data


async def test_reschedule_appointment_logs_initiated_by_from_appointment_record():
    """O evento appointment_rescheduled deve refletir reschedule_initiated_by
    gravado pelo mark_reschedule_in_progress, não apenas o silent_mode atual."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-03-23T09:00:00+00:00",
        "patient_id": "user-1",
        "patients": {"name": "Maria"},
        "reschedule_initiated_by": "clinic",
    })
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(silent_mode=True),
            config=CONFIG,
        )
    logged = [c for c in mock_log_event.call_args_list if c.args[0] == "appointment_rescheduled"]
    assert len(logged) == 1
    assert logged[0].args[2]["initiated_by"] == "clinic"


async def test_reschedule_appointment_respects_modality_restriction():
    """reschedule_appointment deve respeitar modality_restriction do state."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    appt_data = {
        "start_time": "2026-03-20T09:00:00-03:00",
        "patient_id": "user-1",
        "patients": {"name": "Maria"},
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_calendar.SCHEDULE_EXCEPTIONS", {}), \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"):
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-orig",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="online"),
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — should be overridden
        )
    assert mock_update.called
    _, kwargs = mock_update.call_args
    assert kwargs.get("modality") == "online"


async def test_reschedule_appointment_same_datetime_logs_modality_changed():
    """Se new_slot_datetime é o mesmo horário já agendado (só a modalidade muda),
    não deve contar como o reagendamento gratuito do paciente."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    appt_data = {
        "start_time": "2026-03-25T13:00:00+00:00",  # 10:00 em Recife (UTC-3)
        "patient_id": "user-1",
        "patients": {"name": "Maria"},
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        await reschedule_appointment.coroutine(
            appointment_id="evt-abc",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
            modality="online",
        )
    logged_event_types = [call.args[0] for call in mock_log_event.call_args_list]
    assert "modality_changed" in logged_event_types
    assert "appointment_rescheduled" not in logged_event_types


async def test_confirm_appointment_presencial_restriction_on_online_only_slot():
    """Restrição presencial NÃO pode sobrepor slot online-only — deve continuar online."""
    from app.graph.tools import confirm_appointment
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-onlineonly") as mock_create, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "user-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_calendar.get_modality_for_slot", return_value="online"):  # slot is online-only
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),  # restriction says presencial
            config=CONFIG,
            modality="presencial",
        )
    assert "evt-onlineonly" in result
    _, kwargs = mock_create.call_args
    assert kwargs.get("modality") == "online"  # online-only wins over presencial restriction


# ── keep_original_appointment ─────────────────────────────────────────────────

def _keep_original_appt_data(**overrides):
    start = datetime(2026, 8, 31, 9, 0, tzinfo=TZ)
    data = {
        "appointment_id": "evt-old",
        "status": "pending_reschedule",
        "patient_id": "user-1",
        "start_time": start.astimezone(ZoneInfo("UTC")).isoformat(),
        "end_time": (start + timedelta(minutes=60)).astimezone(ZoneInfo("UTC")).isoformat(),
        "modality": "presencial",
        "patients": {"name": "Pedro Lins", "email": "pedro@example.com"},
    }
    data.update(overrides)
    return data


async def test_keep_original_appointment_recreates_event_and_restores_status():
    """Paciente desiste da remarcação com o slot original ainda livre: recria o
    evento no Calendar (horário local de Recife) e volta o status para scheduled,
    sem tocar em booking_fee_paid_at/paid_at (caso Pedro Lins, 31/08/2026 09:00,
    Dr. Júlio — a Eva dizia "consulta mantida" mas nada era revertido)."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    execute.side_effect = [
        MagicMock(data=_keep_original_appt_data()),  # appointment select
        MagicMock(data=[]),                          # update
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-new") as mock_create, \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "mantida" in result.lower()
    assert "evt-new" in result
    mock_create.assert_awaited_once()
    _, create_kwargs = mock_create.call_args
    # start_time do banco (UTC) deve chegar ao Calendar convertido para Recife
    assert create_kwargs["start"] == datetime(2026, 8, 31, 9, 0, tzinfo=TZ)
    assert create_kwargs["slot_minutes"] == 60
    assert create_kwargs["patient_name"] == "Pedro Lins"
    assert create_kwargs["modality"] == "presencial"
    update_data = table.update.call_args[0][0]
    assert update_data["status"] == "scheduled"
    assert update_data["appointment_id"] == "evt-new"
    assert update_data["reschedule_requested_at"] is None
    # taxa/pagamento preservados: o update não pode tocar nesses campos
    assert "booking_fee_paid_at" not in update_data
    assert "paid_at" not in update_data
    mock_notify.assert_called()


async def test_keep_original_appointment_slot_taken_offers_alternatives():
    """Slot original já ocupado por outro paciente: não recria nada, mantém
    pending_reschedule e instrui a Eva a oferecer novos horários."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=_keep_original_appt_data())
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create, \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[
             {"start": "2026-08-31T09:00:00-03:00", "end": "2026-08-31T10:00:00-03:00"}
         ]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "ocupado" in result.lower()
    assert "get_available_slots" in result
    mock_create.assert_not_awaited()
    table.update.assert_not_called()


async def test_keep_original_appointment_already_scheduled_is_noop():
    """Consulta já está scheduled (nada a reverter): não mexe no Calendar nem no banco."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=_keep_original_appt_data(status="scheduled"))
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "já está ativa" in result.lower()
    mock_create.assert_not_awaited()
    table.update.assert_not_called()


async def test_keep_original_appointment_canceled_status_reports_real_status():
    """Consulta cancelada não pode ser "mantida" — a Eva não pode dizer que está tudo certo."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=_keep_original_appt_data(status="canceled"))
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]):
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "cancelada" in result.lower()
    assert "get_available_slots" in result
    table.update.assert_not_called()


async def test_keep_original_appointment_rejects_other_patients_appointment():
    """appointment_id de outro paciente: recusa sem tocar em nada."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=_keep_original_appt_data(patient_id="user-999"))
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]):
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "inválido" in result.lower()
    table.update.assert_not_called()


async def test_keep_original_appointment_past_slot_redirects_to_reschedule():
    """Horário original já passou: impossível manter — instrui a seguir a remarcação."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    past = datetime.now(TZ) - timedelta(days=2)
    execute.return_value = MagicMock(data=_keep_original_appt_data(
        start_time=past.isoformat(),
        end_time=(past + timedelta(minutes=60)).isoformat(),
    ))
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    assert "já passou" in result.lower()
    assert "get_available_slots" in result
    mock_create.assert_not_awaited()
    table.update.assert_not_called()


async def test_keep_original_appointment_calendar_failure_keeps_pending_status():
    """create_event falhou: não pode marcar scheduled sem evento no Calendar —
    o status continua pending_reschedule e a Eva é avisada do erro."""
    from app.graph.tools import keep_original_appointment
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=_keep_original_appt_data())
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools._resolve_doctor", new_callable=AsyncMock, return_value="julio"), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, side_effect=Exception("boom")), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await keep_original_appointment.coroutine(
            appointment_id="evt-old",
            state=_make_state(),
            config=CONFIG,
        )
    assert "Não foi possível" in result
    table.update.assert_not_called()


# ── request_document ──────────────────────────────────────────────────────────

async def test_request_document_inserts_record_and_returns_success():
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_sheets.append_document_request", new_callable=AsyncMock), \
         patch("app.email_sender.send_document_request_email", new_callable=AsyncMock):
        result = await request_document.coroutine(
            document_type="nota_fiscal",
            patient_email="maria@example.com",
            state=_make_state(),
            config=CONFIG,
        )
    assert "nota_fiscal" in result
    assert "✅" in result


async def test_request_document_accepts_requisicao_type():
    """requisicao é um tipo válido de documento (ex.: requisição de acompanhamento psicológico)."""
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.google_sheets.append_document_request", new_callable=AsyncMock) as mock_sheets, \
         patch("app.email_sender.send_document_request_email", new_callable=AsyncMock):
        result = await request_document.coroutine(
            document_type="requisicao",
            patient_email="maria@example.com",
            state=_make_state(),
            config=CONFIG,
        )
    assert "requisicao" in result
    assert "✅" in result
    # planilha recebe o tipo bruto e a clínica é notificada com o rótulo "Requisição"
    assert mock_sheets.await_args.args[4] == "requisicao"
    assert "Requisição" in mock_notify.await_args.kwargs["subject"]


async def test_request_document_accepts_atestado_type():
    """atestado é um tipo válido de documento (ex.: atestado para justificar faltas
    escolares). Caso Bento/Sandro (5581995397978): o pai pediu 'providenciar um
    atestado para apresentar ao colégio' e a Eva só confirmava verbalmente sem
    registrar, porque 'atestado' não estava no enum de request_document."""
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.google_sheets.append_document_request", new_callable=AsyncMock) as mock_sheets, \
         patch("app.email_sender.send_document_request_email", new_callable=AsyncMock):
        result = await request_document.coroutine(
            document_type="atestado",
            patient_email="maria@example.com",
            state=_make_state(),
            config=CONFIG,
        )
    assert "atestado" in result
    assert "✅" in result
    # planilha recebe o tipo bruto e a clínica é notificada com o rótulo "Atestado"
    assert mock_sheets.await_args.args[4] == "atestado"
    assert "Atestado" in mock_notify.await_args.kwargs["subject"]


async def test_request_document_succeeds_even_if_sheets_and_email_fail():
    """Fire-and-forget: sheets/email errors must not surface."""
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_sheets.append_document_request", side_effect=Exception("sheets down")), \
         patch("app.email_sender.send_document_request_email", side_effect=Exception("smtp down")):
        result = await request_document.coroutine(
            document_type="laudo",
            patient_email="maria@example.com",
            state=_make_state(),
            config=CONFIG,
        )
    assert "✅" in result


async def test_request_document_receita_controlada_registra_e_orienta_retirada():
    """Pedido de emissão de receita controlada (ex.: Ritalina) grava a medicação na
    planilha E responde com a orientação de receita física / retirada presencial.

    Caso Davi/Daniel (5582993088617): o pai pediu 'providenciar uma receita para
    buscar', que agora é roteado para request_document em vez de só handoff.
    """
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.google_sheets.append_document_request", new_callable=AsyncMock) as mock_sheets, \
         patch("app.email_sender.send_document_request_email", new_callable=AsyncMock):
        result = await request_document.coroutine(
            document_type="receita",
            patient_email="daniel@example.com",
            state=_make_state(patient_name="Davi", patient_age=14),
            config=CONFIG,
            medication_note="Ritalina LA 40mg",
        )
    # planilha recebe a medicação na coluna de observação (índice 5)
    assert mock_sheets.await_args.args[4] == "receita"
    assert mock_sheets.await_args.args[5] == "Ritalina LA 40mg"
    # resposta orienta retirada presencial (medicação controlada = receita física)
    assert "física" in result.lower()
    assert "retirada" in result.lower()
    # clínica é notificada com o aviso de receita física
    assert "FÍSICA" in mock_notify.await_args.args[0]


async def test_request_document_receita_sem_medicacao_pede_medicacao():
    """Receita sem medicação informada não registra — Eva pergunta qual medicação."""
    from app.graph.tools import request_document
    client, _, _ = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_sheets.append_document_request", new_callable=AsyncMock) as mock_sheets, \
         patch("app.email_sender.send_document_request_email", new_callable=AsyncMock):
        result = await request_document.coroutine(
            document_type="receita",
            patient_email="daniel@example.com",
            state=_make_state(medication_note=None),
            config=CONFIG,
        )
    assert "medicação" in result.lower()
    mock_sheets.assert_not_awaited()


# ── transfer_to_human ─────────────────────────────────────────────────────────

async def test_transfer_to_human_deactivates_user():
    from app.graph.tools import transfer_to_human
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock):
        result = await transfer_to_human.coroutine(
            reason="Paciente quer falar com humano",
            state=_make_state(),
            config=CONFIG,
        )
    mock_upsert.assert_awaited_once()
    call_kwargs = mock_upsert.call_args[0]
    assert call_kwargs[1]["active"] is False
    assert "deactivated_at" in call_kwargs[1]
    assert "atendente" in result.lower()


async def test_transfer_to_human_adds_private_note_to_chatwoot():
    """On human transfer, a private note with patient context is added to Chatwoot."""
    from app.graph.tools import transfer_to_human
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation(PHONE, 42)

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.graph.tools.unassign_agent_bot", new_callable=AsyncMock), \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await transfer_to_human.coroutine(
            reason="Paciente escolheu presencial",
            state=_make_state(),
            config=CONFIG,
        )
    mock_note.assert_awaited_once()
    note_text = mock_note.call_args[0][1]
    assert "Transferido pelo bot" in note_text
    assert "Paciente escolheu presencial" in note_text


async def test_transfer_to_human_sends_only_to_user():
    """transfer_to_human returns the message directly (no send_text call); message goes to patient via LangGraph."""
    from app.graph.tools import transfer_to_human
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock) as mock_send, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock):
        result = await transfer_to_human.coroutine(
            reason="teste",
            state=_make_state(),
            config=CONFIG,
        )
    assert mock_send.await_count == 0
    assert "transferir" in result.lower() or "encaminhar" in result.lower()


# ── confirm_attendance ────────────────────────────────────────────────────────

async def test_confirm_attendance_sets_confirmed_at():
    from app.graph.tools import confirm_attendance
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=[{"confirmed_at": None, "status": "scheduled"}])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await confirm_attendance.coroutine(
            appointment_id="evt-abc123",
            state=_make_state(),
            config=CONFIG,
        )
    assert "confirmada" in result.lower()
    # Verify the update was called with confirmed_at
    update_call = table.update.call_args[0][0]
    assert "confirmed_at" in update_call


async def test_confirm_appointment_copies_booking_fee_waived_to_appointment():
    """When user has booking_fee_waived=True, the appointment row gets booking_fee_waived=True
    and booking_fee_paid_at is set immediately. Return string must NOT instruct PIX payment."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    mock_user = {"id": "user-wv", "booking_fee_waived": True, "custom_price": None}
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock, return_value="evt-waived"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[mock_user]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=mock_user), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-07-09T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    # Return string must NOT contain PIX instructions
    assert "PIX" not in result
    assert "taxa de reserva" not in result.lower()
    # DB insert must include booking_fee_waived=True and non-null booking_fee_paid_at
    insert_call_data = table.insert.call_args[0][0]
    assert insert_call_data["booking_fee_waived"] is True
    assert insert_call_data["booking_fee_paid_at"] is not None


# ── _expected_consultation_amount ────────────────────────────────────────────

def test_expected_consultation_amount_price_override():
    """price_override is the patient's custom CARD price — the R$50 PIX/cash
    discount still applies on top of it, except for courtesy (0)."""
    from app.graph.tools import _expected_consultation_amount
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 6, 1, tzinfo=ZoneInfo("America/Recife"))
    # Baseline: Dr. Júlio adult post-June → 700 - 50 = 650
    assert _expected_consultation_amount("julio", 35, None, now) == 650
    # price_override=500 (card price): returns 500 - 50 = 450 (PIX/cash discount applies)
    assert _expected_consultation_amount("julio", 35, None, now, price_override=500) == 450
    # price_override=0: returns 0 (courtesy, no discount math)
    assert _expected_consultation_amount("julio", 35, None, now, price_override=0) == 0
    # price_override=None: standard formula still applies
    assert _expected_consultation_amount("bruna", 40, None, now, price_override=None) == 650


# ── register_payment ──────────────────────────────────────────────────────────

def _make_supabase_client_with_appointment(start_time="2026-03-23T09:00:00+00:00", end_time="2026-03-23T10:00:00+00:00"):
    """Supabase client that serves register_payment's two sequential appointment queries.

    Call order:
      1. appts_result — appointments joined with users (patient resolution)
      2. appt_result  — full appointment details (payment logic)
      3+. update/upsert/linked-appts → generic empty response

    Defaults to a start_time already in the past relative to the fixed "today"
    used across these tests, so the default fixture exercises the
    already-occurred payment-timing branch unless overridden with a future date.
    """
    # Call 1: new appointment-centric query with users join
    appts_with_users = MagicMock(data=[{
        "appointment_id": "apt-1",
        "start_time": start_time,
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "status": "scheduled",
        "patients": {"id": "user-123", "name": "Maria"},
    }])
    # Call 2: full appointment fetch for payment logic
    apt_data = MagicMock(data=[{
        "appointment_id": "apt-1",
        "start_time": start_time,
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "end_time": end_time,
        "paid_at": None,
        "booking_fee_paid_at": None,
        "status": "scheduled",
        "consultation_type": "retorno",
    }])
    empty = MagicMock(data=[])

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        # Call 1: appts_with_users (patient resolution — appointment + users join)
        if _side_effect.call_count == 1:
            return appts_with_users
        # Call 2: scheduled_raw (PRIORITY 1) → found scheduled appointment to pay
        if _side_effect.call_count == 2:
            return apt_data
        return empty
    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert", "is_"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


async def test_register_payment_appends_sheet_and_notifies():
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )

    assert "✅" in result
    mock_sheets.assert_awaited_once()
    sheets_kwargs = mock_sheets.call_args
    assert "Maria" in sheets_kwargs[0][0]          # patient_name
    assert "100,00" in sheets_kwargs[0][4]         # amount
    assert "https://drive.google.com" in sheets_kwargs[0][5]  # drive_link
    mock_notify.assert_called()
    notify_msg = mock_notify.call_args[0][0]       # message is first positional arg
    assert "Maria" in notify_msg
    assert "https://drive.google.com" in notify_msg


async def test_register_payment_rename_failure_still_succeeds():
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.google_drive.rename_file", new_callable=AsyncMock, side_effect=Exception("Drive unavailable")), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert "✅" in result
    # A failed rename must not go unnoticed — the clinic notification should flag
    # that the Drive filename may not match this patient/payment.
    notify_msg = mock_notify.call_args[0][0]
    assert "não pôde ser renomeado" in notify_msg


async def test_register_payment_sheets_append_failure_notifies_clinic():
    """A failed write to the Pagamentos sheet must not go unnoticed — the clinic
    notification should flag that the payment was NOT recorded in the spreadsheet,
    even though the appointment fields were updated and Eva reports success to the
    patient (caso Ana Patrícia De Souza, 2026-07-20: three payments processed with
    booking_fee_paid_at set and event logged, but silently missing from the sheet)."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock, side_effect=Exception("Sheets API unavailable")), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert "✅" in result
    notify_msg = mock_notify.call_args[0][0]
    assert "NÃO" in notify_msg and "planilha" in notify_msg


async def test_register_payment_rename_uses_no_extension_and_sanitizes_amount():
    """The filename passed to rename_file must have no extension (rename_file now
    preserves whatever extension the file was actually uploaded with) and the
    amount portion must use hyphens instead of commas/dots."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock) as mock_rename, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="R$ 100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    new_filename = mock_rename.call_args[0][1]
    assert "." not in new_filename
    assert "," not in new_filename
    assert "100-00" in new_filename


async def test_register_payment_forwards_resolved_drive_filename_to_sheet():
    """The extension is only known after the rename (rename_file reads the current
    name from Drive), so register_payment must hand the resolved name to
    append_payment_receipt instead of letting the sheet rebuild its own — otherwise
    the comprovante text in column I never matches the file (caso PDF: sheet dizia
    .jpg; e a vírgula do valor divergia do hífen do nome real)."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    resolved = "Maria_23-03-2026_R$100-00.pdf"
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock, return_value=resolved), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="R$ 100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert mock_sheets.call_args.kwargs["receipt_filename"] == resolved


async def test_register_payment_rename_failure_leaves_sheet_filename_unresolved():
    """If the rename failed, there is no resolved name to show — pass none through so
    append_payment_receipt falls back to the canonical stem instead of displaying a
    name the Drive file does not have."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock, side_effect=Exception("Drive unavailable")), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="R$ 100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert mock_sheets.call_args.kwargs["receipt_filename"] == ""


def _make_supabase_client_with_canceled_appointment(slot_taken=False):
    """Supabase client for the late-payment recovery path: the patient has NO
    scheduled appointment, only a canceled one with a pending booking fee whose
    date is still in the future.

    Call order:
      1. appts_with_users → empty (no scheduled appointment for this phone)
      2. canceled_result  → the canceled appointment awaiting the fee
      3. conflict check   → empty when the slot is still free
    """
    future_start = (datetime.now(TZ) + timedelta(days=12)).replace(hour=16, minute=0, second=0, microsecond=0)
    future_end = future_start + timedelta(hours=1)
    canceled = MagicMock(data=[{
        "appointment_id": "apt-canceled-1",
        "start_time": future_start.isoformat(),
        "end_time": future_end.isoformat(),
        "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd",
        "status": "canceled",
        "patients": {"id": "user-123", "name": "Mismania Karla Pereira", "birth_date": "14/01/1980"},
    }])
    conflict = MagicMock(data=[{"id": "other-appt"}] if slot_taken else [])
    empty = MagicMock(data=[])

    table = MagicMock()

    # Honour the select() projection: a column the query never asked for must not
    # show up in the result. Without this the mock hands back every key and a
    # missing column in the real select goes unnoticed.
    selected: dict[str, str] = {"cols": ""}

    def _select(cols="*", *_a, **_kw):
        selected["cols"] = cols
        return table

    def _project(rows):
        cols = selected["cols"]
        if "*" in cols:
            return rows
        return [{k: v for k, v in row.items() if k in cols} for row in rows]

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        if _side_effect.call_count == 1:
            return empty       # no scheduled appointment
        if _side_effect.call_count == 2:
            return MagicMock(data=_project(canceled.data))  # canceled, fee pending
        if _side_effect.call_count == 3:
            return conflict    # slot conflict check
        return empty
    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    for m in ("eq", "in_", "limit", "single", "maybe_single",
              "gte", "gt", "lt", "neq", "order", "insert", "update", "upsert", "is_"):
        getattr(table, m).return_value = table
    table.select = MagicMock(side_effect=_select)
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client


async def test_register_payment_recovers_canceled_appointment_with_free_slot():
    """Fee paid after the appointment was auto-canceled for non-payment: the
    canceled-appointment query must select end_time, which the slot-conflict
    check reads. Omitting it raised KeyError('end_time') and the whole payment
    registration crashed — the exact path of a patient who pays late.
    """
    from app.graph.tools import register_payment
    client = _make_supabase_client_with_canceled_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Mismania Karla Pereira"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(preferred_doctor="bruna"),
            config=CONFIG,
        )

    assert "CONSULTA_CANCELADA_REATIVAVEL" in result
    assert "apt-canceled-1" in result
    assert "Mismania Karla Pereira" in result


async def test_register_payment_canceled_appointment_slot_already_taken():
    """Same recovery path, but the freed slot was taken by someone else in the
    meantime — Eva must be told to arrange a new date instead of reactivating."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_with_canceled_appointment(slot_taken=True)
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Mismania Karla Pereira"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(preferred_doctor="bruna"),
            config=CONFIG,
        )

    assert "CONSULTA_CANCELADA_SEM_SLOT" in result
    assert "pending_reschedule" in result


async def test_register_payment_reactivation_slot_taken_returns_marker():
    """Branch slot-taken da reativação via patient_name_override (tools.py ~2912,
    caso Ricardo José Vieira Cunha Filho, 10/08/2026): o horário original já tem
    evento no Calendar → status vira pending_reschedule com booking_fee_paid_at no
    mesmo instante, e o retorno TEM de conter REACTIVATION_SLOT_TAKEN_MARKER — é
    por esse fragmento que o patient_agent_node detecta o resultado e o envia ao
    paciente verbatim, sem re-síntese pela LLM. Reescrever a mensagem sem o marker
    desligaria o guard silenciosamente."""
    from app.graph.tools import register_payment, REACTIVATION_SLOT_TAKEN_MARKER

    julio_id = "d5baa58b-a788-4f40-b8c0-512c189150be"
    slot_start = (datetime.now(TZ) + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    slot_end = slot_start + timedelta(hours=1)

    results = [
        # 1. patients ilike (patient_name_override)
        MagicMock(data=[{"id": "user-123", "name": "Ricardo José Vieira Cunha Filho", "doctor_id": julio_id}]),
        # 2. PRIORITY 1: scheduled → nenhum
        MagicMock(data=[]),
        # 3. PRIORITY 2: canceled futuro com taxa pendente → existe (defere à reativação)
        MagicMock(data=[{"appointment_id": "apt-ricardo"}]),
        # 4. canceled_result da reativação
        MagicMock(data=[{
            "appointment_id": "apt-ricardo",
            "start_time": slot_start.isoformat(),
            "end_time": slot_end.isoformat(),
            "doctor_id": julio_id,
            "modality": "presencial",
        }]),
        # 5. doctors.agenda_id (.single())
        MagicMock(data={"agenda_id": "cal-julio"}),
        # 6. update → pending_reschedule + booking_fee_paid_at
        MagicMock(data=[]),
        # 7. patients.custom_price (maybe_single)
        MagicMock(data={}),
    ]
    execute = AsyncMock(side_effect=results)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single", "ilike",
              "gte", "gt", "lt", "neq", "order", "insert", "update", "upsert", "is_"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "contact-1"}), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=[{"id": "user-123"}]), \
         patch("app.patients.get_contacts_for_patient", new_callable=AsyncMock, return_value=[{"phone": "5581988912861"}]), \
         patch("app.google_calendar._credentials", return_value=MagicMock()), \
         patch("googleapiclient.discovery.build", return_value=MagicMock()), \
         patch("app.google_calendar._get_busy", return_value=[
             {"start": slot_start.isoformat(), "end": slot_end.isoformat()},
         ]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
            patient_name_override="Ricardo José Vieira Cunha Filho",
        )

    assert REACTIVATION_SLOT_TAKEN_MARKER in result
    # Nada de linguagem de sucesso da reativação — o horário foi perdido.
    assert "reagendada" not in result
    assert "garantida" not in result
    # Assinatura do branch: pending_reschedule e taxa gravadas no mesmo update.
    update_payloads = [c.args[0] for c in table.update.call_args_list if c.args]
    assert any(
        p.get("status") == "pending_reschedule" and p.get("booking_fee_paid_at")
        for p in update_payloads
    ), f"update pending_reschedule não encontrado: {update_payloads}"


async def test_register_payment_rename_unknown_amount_uses_placeholder():
    """amount='?' (not identified) must not produce a broken filename like
    '..._R$.pdf' or '..._R$?.pdf' — falls back to a readable placeholder."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock) as mock_rename, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="?",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    new_filename = mock_rename.call_args[0][1]
    assert "valor-nao-identificado" in new_filename
    assert "?" not in new_filename


def _make_supabase_client_for_override(candidates: list[dict]):
    """Supabase client for patient_name_override tests: call 1 is the `patients`
    ilike search (returns `candidates`), call 2 is the scheduled-appointment lookup
    (found), the rest are empty."""
    ilike_result = MagicMock(data=candidates)
    apt_data = MagicMock(data=[{
        "appointment_id": "apt-1",
        "start_time": "2026-03-23T09:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "end_time": "2026-03-23T10:00:00+00:00",
        "paid_at": None,
        "booking_fee_paid_at": None,
        "status": "scheduled",
        "consultation_type": "retorno",
    }])
    empty = MagicMock(data=[])

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        if _side_effect.call_count == 1:
            return ilike_result
        if _side_effect.call_count == 2:
            return apt_data
        return empty
    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert", "is_", "ilike"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client


async def test_register_payment_override_ambiguous_name_asks_for_clarification():
    """Regression: `ilike("%Francisco%")` can match several unrelated patients
    (e.g. 'Francisco Fonseca Lima' and 'Francisco Domingues Bruno de Faria').
    Silently taking candidates[0] misattributed a real payment to the wrong
    patient (case: Francisco Domingues, 2026-07-03). With no way to tell which
    candidate is right, register_payment must ask instead of guessing."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_for_override([
        {"id": "wrong-id", "name": "Francisco Fonseca Lima", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be"},
        {"id": "right-id", "name": "Francisco Domingues Bruno de Faria", "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd"},
    ])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            patient_name_override="Francisco",
            state=_make_state(),
            config=CONFIG,
        )

    assert "Francisco Fonseca Lima" in result
    assert "Francisco Domingues Bruno de Faria" in result
    mock_sheets.assert_not_awaited()  # must not register the payment against either candidate


async def test_register_payment_multiple_patients_on_phone_demands_new_tool_call():
    """Regressão (Juliana, 5581981845995, 04/08/2026): o telefone tem duas
    pacientes (mãe e filha), então register_payment devolveu só a pergunta
    'Para qual deles é o comprovante?'. Eva perguntou à paciente em forma de
    sim/não, ela respondeu 'Sim' — e Eva escreveu 'sua taxa de reserva foi
    recebida e sua consulta está garantida' SEM chamar register_payment de novo.
    booking_fee_paid_at ficou nulo e o cron de cobrança disparou ~2h depois,
    depois de Eva já ter confirmado o recebimento.

    O retorno da desambiguação precisa dizer explicitamente que NADA foi
    registrado e que a tool tem de ser chamada de novo com
    patient_name_override — inclusive quando a resposta do paciente for um
    'sim' a uma pergunta fechada."""
    from app.graph.tools import register_payment
    _mae = {"id": "mae-id", "patient_name": "Juliana Fernandes Feitosa de Souza"}
    _filha = {"id": "filha-id", "patient_name": "Maria Júlia Fernandes Feitosa Lucena"}
    client, _table, _execute = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[_mae, _filha]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )

    assert "Juliana Fernandes Feitosa de Souza" in result
    assert "Maria Júlia Fernandes Feitosa Lucena" in result
    mock_sheets.assert_not_awaited()
    # o retorno é instrução interna, não texto pronto pro paciente
    assert "INSTRUÇÃO INTERNA" in result
    # e precisa exigir a nova chamada da tool, não só a pergunta
    assert "register_payment" in result
    assert "patient_name_override" in result


def _make_supabase_client_self_path_old_completed():
    """Supabase client for the SELF-PATH late-balance case: the patient pays the
    saldo from their OWN number (no patient_name_override), and their only
    appointment is a COMPLETED consultation more than 15 days ago whose booking
    fee was already paid.

    The mock is date-aware for every appointment lookup that applies a
    `.gte("start_time", ...)` lower bound: if the window starts after the consult
    date (e.g. now-15d for a 35-day-old consult) it hides the appointment, exactly
    like the real database. Two windows matter here:
      - the SELF-PATH resolution query (`appts_result`) — hiding the appt made
        `seen_users` empty, so Eva asked "Para qual paciente é este comprovante?"
        even though the phone has a single, unambiguous patient (caso Danniela,
        5581991950147, same root as the override-path double-fee bug);
      - PRIORITY 3 (`completed_raw`) — hiding it made expected_remaining the full
        price instead of price-100.

    Call order (client.from_):
      1. appts_result   — resolution (appointments + patients join)
      2. scheduled_raw  — empty (no active appointment)         [PRIORITY 1]
      3. future_canceled — empty (nothing to reactivate)        [PRIORITY 2]
      4. completed_raw  — the old completed appt, iff window includes it [PRIORITY 3]
      5. patients custom_price → None
      6+. updates / misc → empty
    """
    now = datetime.now(TZ)
    consult_start = now - timedelta(days=35)      # consultation already happened, >15d ago
    resolution_appt = {
        "appointment_id": "apt-old-completed",
        "start_time": consult_start.isoformat(),
        "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd",  # Dra. Bruna
        "status": "completed",
        "patients": {"id": "dan-id", "name": "Danniela Azevedo Ramos De Almeida"},
    }
    completed_appt = {
        "appointment_id": "apt-old-completed",
        "start_time": consult_start.isoformat(),
        "end_time": (consult_start + timedelta(hours=1)).isoformat(),
        "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd",
        "paid_at": None,
        "booking_fee_paid_at": (now - timedelta(days=58)).isoformat(),  # paid a previous month
        "status": "completed",
        "consultation_type": None,
        "booking_fee_waived": False,
    }
    empty = MagicMock(data=[])

    captured = {"start_gte": None}

    def _gte(col, val):
        if col == "start_time":
            captured["start_gte"] = val
        return table

    def _hidden():
        lb = captured["start_gte"]
        return lb is not None and datetime.fromisoformat(lb) > consult_start

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        n = _side_effect.call_count
        if n == 1:
            hidden = _hidden()
            captured["start_gte"] = None
            return empty if hidden else MagicMock(data=[resolution_appt])   # appts_result
        if n in (2, 3):
            captured["start_gte"] = None
            return empty                                                    # PRIORITY 1 / 2
        if n == 4:
            hidden = _hidden()
            captured["start_gte"] = None
            return empty if hidden else MagicMock(data=[completed_appt])    # PRIORITY 3
        captured["start_gte"] = None
        return empty

    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "order", "insert", "update", "upsert", "is_", "gt", "lt", "neq", "ilike"):
        getattr(table, m).return_value = table
    table.gte = MagicMock(side_effect=_gte)
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client


async def test_register_payment_self_path_resolves_old_completed_appointment():
    """Self-path (patient pays from their own number, no name override): a saldo
    for a consultation that happened more than 15 days ago must resolve to that
    patient and settle the consultation — NOT trigger "Para qual paciente é este
    comprovante?" nor charge the booking fee again.

    Same root as caso Danniela (5581991950147, 12/08/2026), but in the SELF-PATH
    resolution query, which bounded appointments to now-15d. The phone has a
    single unambiguous patient, so the 15-day window only ever hid the right
    answer — it was never needed for disambiguation (that is handled earlier from
    get_users_by_phone). With the window gone, R$550 QUITA a R$650 consult whose
    R$100 fee was already paid."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_self_path_old_completed()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[{"id": "dan-id", "patient_name": "Danniela Azevedo Ramos De Almeida"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(preferred_doctor="bruna"),
            config=CONFIG,
        )

    assert "Para qual paciente" not in result
    mock_sheets.assert_awaited_once()
    assert mock_sheets.call_args.kwargs["payment_type"] == "Consulta"
    assert "QUITADA" in result
    assert "Pagamento Parcial" not in result


async def test_register_payment_override_ambiguous_name_demands_new_tool_call():
    """Mesmo buraco do teste acima, no ramo de nome ambíguo (ilike com vários
    candidatos): a pergunta sozinha deixa Eva livre pra 'confirmar' o pagamento
    sem registrar nada."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_for_override([
        {"id": "wrong-id", "name": "Francisco Fonseca Lima", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be"},
        {"id": "right-id", "name": "Francisco Domingues Bruno de Faria", "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd"},
    ])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            patient_name_override="Francisco",
            state=_make_state(),
            config=CONFIG,
        )

    assert "INSTRUÇÃO INTERNA" in result
    assert "register_payment" in result
    assert "patient_name_override" in result


async def test_register_payment_override_disambiguates_via_sender_contact_link():
    """When multiple patients share the search name, but the sender's phone is
    already linked (patient_contacts) to exactly one of them, use that one
    instead of asking — this is the common case (a guardian paying for their
    own registered dependent)."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_for_override([
        {"id": "wrong-id", "name": "Francisco Fonseca Lima", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be"},
        {"id": "right-id", "name": "Francisco Domingues Bruno de Faria", "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd"},
    ])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "contact-1"}), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=[{"id": "right-id", "name": "Francisco Domingues Bruno de Faria"}]), \
         patch("app.patients.get_contacts_for_patient", new_callable=AsyncMock, return_value=[{"phone": "5511900000000"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            patient_name_override="Francisco",
            state=_make_state(),
            config=CONFIG,
        )

    assert "✅" in result
    mock_sheets.assert_awaited_once()
    assert "Francisco Domingues Bruno de Faria" in mock_sheets.call_args[0][0]


async def test_register_payment_override_unlinked_sender_requires_confirmation():
    """A UNIQUE ilike match is not proof of identity — the sender could type a
    name that happens to match a different patient's registration. If the
    sender's phone has no known link (patient_contacts) to the matched patient,
    register_payment must ask for explicit confirmation instead of silently
    filing the payment under that patient's name."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_for_override([
        {"id": "some-id", "name": "Maria Eduarda Souza", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be"},
    ])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            patient_name_override="Maria Eduarda Souza",
            state=_make_state(),
            config=CONFIG,
        )

    assert "Maria Eduarda Souza" in result
    mock_sheets.assert_not_awaited()


async def test_register_payment_override_unlinked_sender_confirmed_registers():
    """Once the attendant/Eva has confirmed with the sender that the receipt is
    really for that patient, passing sender_confirmed_patient=True must bypass
    the patient_contacts link requirement and register the payment normally."""
    from app.graph.tools import register_payment
    client = _make_supabase_client_for_override([
        {"id": "some-id", "name": "Maria Eduarda Souza", "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be"},
    ])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.patients.get_contacts_for_patient", new_callable=AsyncMock, return_value=[{"phone": "5511900000000"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            patient_name_override="Maria Eduarda Souza",
            sender_confirmed_patient=True,
            state=_make_state(),
            config=CONFIG,
        )

    assert "✅" in result
    mock_sheets.assert_awaited_once()
    assert "Maria Eduarda Souza" in mock_sheets.call_args[0][0]


def _make_supabase_client_with_old_completed_appointment():
    """Supabase client for the LATE-BALANCE path: the patient has NO scheduled and
    NO future-canceled appointment — only a COMPLETED consultation that happened
    more than 15 days ago, whose booking fee was already paid (in a previous month).

    The mock is date-aware for the completed-appointment lookup: it honours the
    `.gte("start_time", ...)` lower bound the query applies. A lookback window that
    starts after the consultation date (e.g. now-15d for a 35-day-old consult) hides
    the appointment, exactly as the real database does. This is what let Eva ignore
    the already-paid R$100 booking fee and charge it a second time (caso Danniela
    Azevedo, 5581991950147, 2026-08-12).

    Call order (client.from_):
      1. patients ilike  → the single candidate (name-override resolution)
      2. scheduled_raw   → empty (no active appointment)
      3. future_canceled → empty (nothing to reactivate)
      4. completed_raw   → the old completed appt, IFF the query's date window includes it
      5. patients custom_price → empty → None
      6+. updates / misc → empty
    """
    now = datetime.now(TZ)
    consult_start = now - timedelta(days=35)      # consultation already happened, >15d ago
    candidate = {
        "id": "dan-id",
        "name": "Danniela Azevedo Ramos De Almeida",
        "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd",  # Dra. Bruna
    }
    completed_appt = {
        "appointment_id": "apt-old-completed",
        "start_time": consult_start.isoformat(),
        "end_time": (consult_start + timedelta(hours=1)).isoformat(),
        "doctor_id": "18b01f87-eacd-4905-bd4a-a8293991e6fd",
        "paid_at": None,
        "booking_fee_paid_at": (now - timedelta(days=58)).isoformat(),  # paid a previous month
        "status": "completed",
        "consultation_type": None,
        "booking_fee_waived": False,
    }
    empty = MagicMock(data=[])

    captured = {"start_gte": None}

    def _gte(col, val):
        if col == "start_time":
            captured["start_gte"] = val
        return table

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        n = _side_effect.call_count
        if n == 1:
            captured["start_gte"] = None
            return MagicMock(data=[candidate])          # ilike patients
        if n == 2:
            captured["start_gte"] = None
            return empty                                # scheduled_raw (PRIORITY 1)
        if n == 3:
            captured["start_gte"] = None
            return empty                                # future_canceled (PRIORITY 2)
        if n == 4:
            # completed_raw (PRIORITY 3): a start_time lower bound after the consult
            # date hides it — reproducing the 15-day-window bug.
            lb = captured["start_gte"]
            captured["start_gte"] = None
            hidden = lb is not None and datetime.fromisoformat(lb) > consult_start
            return empty if hidden else MagicMock(data=[completed_appt])
        return empty

    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "order", "insert", "update", "upsert", "is_", "gt", "lt", "neq", "ilike"):
        getattr(table, m).return_value = table
    table.gte = MagicMock(side_effect=_gte)
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client


async def test_register_payment_settles_saldo_of_completed_appt_older_than_15_days():
    """The saldo of a consultation that happened more than 15 days ago must settle
    the consultation — NOT be charged the booking fee again.

    Danniela's consult (Dra. Bruna, R$650) had its R$100 booking fee paid in June.
    On 12/08 she paid the R$550 saldo. Because the completed-appointment lookup was
    bounded to the last 15 days, the 35-day-old consult was invisible, so Eva did
    not know the fee was paid: she treated R$550 as a partial payment still owing
    R$100 and asked for it again (double booking fee). With the appointment found,
    expected_remaining is 650-100=550, so R$550 QUITA the consultation.
    """
    from app.graph.tools import register_payment
    client = _make_supabase_client_with_old_completed_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.patients.get_contact_by_phone", new_callable=AsyncMock, return_value={"id": "contact-1"}), \
         patch("app.patients.get_patients_by_contact", new_callable=AsyncMock, return_value=[{"id": "dan-id"}]), \
         patch("app.patients.get_contacts_for_patient", new_callable=AsyncMock, return_value=[{"phone": "5581991950147"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            patient_name_override="Danniela Azevedo Ramos De Almeida",
            state=_make_state(preferred_doctor="bruna"),
            config=CONFIG,
        )

    # R$550 must settle the consultation, not read as a partial payment.
    mock_sheets.assert_awaited_once()
    assert mock_sheets.call_args.kwargs["payment_type"] == "Consulta"
    assert "QUITADA" in result
    assert "Pagamento Parcial" not in result


# ── _parse_brl_amount ──────────────────────────────────────────────────────────

def test_parse_brl_amount_comma_decimal():
    from app.graph.tools import _parse_brl_amount
    assert _parse_brl_amount("100,00") == 100.0


def test_parse_brl_amount_dot_decimal():
    """A plain/US-style dot decimal ('100.00') must NOT be mangled into 10000.0
    by treating the dot as a thousands separator."""
    from app.graph.tools import _parse_brl_amount
    assert _parse_brl_amount("100.00") == 100.0


def test_parse_brl_amount_thousands_with_comma_decimal():
    from app.graph.tools import _parse_brl_amount
    assert _parse_brl_amount("1.200,00") == 1200.0


def test_parse_brl_amount_with_currency_prefix_and_spaces():
    from app.graph.tools import _parse_brl_amount
    assert _parse_brl_amount("R$ 650,00") == 650.0


def test_parse_brl_amount_unidentified_returns_zero():
    from app.graph.tools import _parse_brl_amount
    assert _parse_brl_amount("?") == 0.0
    assert _parse_brl_amount("") == 0.0


async def test_register_payment_silent_mode_recovers_drive_link_from_history():
    """Attendant note (silent_mode=True) asking to register an existing receipt:
    drive_link="" should be recovered by scanning recent conversation messages."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    state = _make_state(
        silent_mode=True,
        messages=[
            HumanMessage(content="[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00 [drive_link:https://drive.google.com/file/d/xyz789/view]"),
            HumanMessage(content="[Instrução da atendente]: pode registrar o comprovante acima"),
        ],
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock) as mock_rename, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="100,00",
            drive_link="",
            state=state,
            config=CONFIG,
        )
    mock_rename.assert_awaited_once()
    assert mock_rename.call_args[0][0] == "xyz789"
    sheets_kwargs = mock_sheets.call_args
    assert "https://drive.google.com/file/d/xyz789/view" in sheets_kwargs[0][5]  # drive_link


async def test_register_payment_patient_insists_already_sent_recovers_from_history():
    """Patient claims 'já enviei, está aqui!' on a later turn with no new image
    attached — drive_link is empty and there's no silent_mode/attendant note involved.
    The bot previously missed/ignored the image; register_payment must still recover
    the receipt link by scanning recent conversation history (not just attendant-note
    triggered calls), otherwise it wrongly tells the patient nothing was received."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    state = _make_state(
        messages=[
            HumanMessage(content="[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00 [drive_link:https://drive.google.com/file/d/abc123/view]"),
            HumanMessage(content="eu enviei sim, está aqui!"),
        ],
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock) as mock_rename, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheets, \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="100,00",
            drive_link="",
            state=state,
            config=CONFIG,
        )
    mock_rename.assert_awaited_once()
    assert mock_rename.call_args[0][0] == "abc123"
    sheets_kwargs = mock_sheets.call_args
    assert "https://drive.google.com/file/d/abc123/view" in sheets_kwargs[0][5]


async def test_register_payment_is_link_skips_history_scan_even_in_silent_mode():
    """is_link=True payments (attendant-confirmed via 'PAGAMENTO CONFIRMADO') intentionally
    have no receipt image — even in silent_mode, drive_link must stay empty rather than
    being backfilled from an unrelated older comprovante in the conversation."""
    from app.graph.tools import register_payment
    client, _, _ = _make_supabase_client_with_appointment()
    state = _make_state(
        silent_mode=True,
        messages=[
            HumanMessage(content="[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00 [drive_link:https://drive.google.com/file/d/old222/view]"),
        ],
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock) as mock_rename, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        await register_payment.coroutine(
            amount="600,00",
            drive_link="",
            is_link=True,
            state=state,
            config=CONFIG,
        )
    mock_rename.assert_not_awaited()


async def test_transfer_to_human_unassigns_chatwoot_bot(mock_send_text):
    """When bot hands off to human, Chatwoot agent bot is unassigned for that conversation."""
    from app.graph.tools import transfer_to_human
    from app.chatwoot import register_conversation, _store
    _store.clear()
    register_conversation("5511999999999@s.whatsapp.net", 77)

    config = {
        "configurable": {
            "phone": "5511999999999@s.whatsapp.net",
            "thread_id": "5511999999999@s.whatsapp.net",
        }
    }
    state = {"user_name": "João", "patient_name": "João"}

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock), \
         patch("app.graph.tools.unassign_agent_bot", new_callable=AsyncMock) as mock_unassign:
        await transfer_to_human.ainvoke(
            {"reason": "paciente quer falar com atendente", "state": state},
            config=config,
        )
        mock_unassign.assert_called_once_with(77)


async def test_register_payment_sets_booking_fee_paid_at():
    """R$100 payment should set booking_fee_paid_at (taxa de reserva), not paid_at."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    # R$100 → taxa de reserva: only booking_fee_paid_at should be set, not paid_at
    update_calls = [c for c in table.update.call_args_list if "booking_fee_paid_at" in c[0][0]]
    assert len(update_calls) == 1
    paid_at_calls = [c for c in table.update.call_args_list if "paid_at" in c[0][0] and "booking_fee_paid_at" not in c[0][0]]
    assert len(paid_at_calls) == 0
    assert "taxa de reserva registrada" in result
    # The fixture's appointment is already in the past (2026-03-23, before "today"),
    # so the note must say the balance can be settled now — never "no dia da consulta"
    # (caso Geórgia, 2026-07-21: Eva told a patient with a past appointment to pay
    # "no dia da consulta" as if it hadn't happened yet).
    assert "já ocorreu" in result
    assert "no dia da consulta" not in result


async def test_register_payment_booking_fee_future_appointment_says_no_dia_da_consulta():
    """When the appointment is still in the future, the balance note should keep
    saying it's due "no dia da consulta" — only past appointments get the
    already-occurred wording."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment(
        start_time="2026-12-23T09:00:00+00:00", end_time="2026-12-23T10:00:00+00:00",
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    assert "no dia da consulta" in result
    assert "já ocorreu" not in result


async def test_register_payment_full_amount_sets_paid_at():
    """Full payment (>= expected) should set both paid_at and booking_fee_paid_at."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="550,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(),
            config=CONFIG,
        )
    # Full payment: both paid_at and booking_fee_paid_at should be set
    update_calls = [c for c in table.update.call_args_list if "paid_at" in c[0][0]]
    assert len(update_calls) == 1
    assert "QUITADA" in result


# ── consultar_data ────────────────────────────────────────────────────────────

async def test_consultar_data_full_date():
    from app.graph.tools import consultar_data
    # 2026-09-15 is a Tuesday
    result = await consultar_data.coroutine(data="15/09/2026")
    assert "15/09/2026" in result
    assert "terça-feira" in result


async def test_consultar_data_today_and_tomorrow():
    from app.graph.tools import consultar_data
    now = datetime.now(TZ)
    today_str = now.strftime("%d/%m/%Y")
    tomorrow_str = (now + timedelta(days=1)).strftime("%d/%m/%Y")
    assert "(hoje)" in await consultar_data.coroutine(data=today_str)
    assert "(amanhã)" in await consultar_data.coroutine(data=tomorrow_str)


async def test_consultar_data_dd_mm_infers_future_year():
    from app.graph.tools import consultar_data
    now = datetime.now(TZ)
    # A date far behind in the year should resolve to a future occurrence,
    # never to a past date.
    result = await consultar_data.coroutine(data="01/01")
    # The output year is today's year or next year, and the relative part is
    # a future "(em N dias)" or "(hoje)" — never "atrás".
    assert "atrás" not in result


async def test_consultar_data_invalid_input():
    from app.graph.tools import consultar_data
    result = await consultar_data.coroutine(data="banana")
    assert "dd/mm" in result


async def test_consultar_data_leap_day_dd_mm():
    from app.graph.tools import consultar_data
    result = await consultar_data.coroutine(data="29/02")
    # Must resolve to a real Feb 29 (next leap year), not the invalid-input message.
    assert "29/02" in result
    assert "Não consegui entender" not in result


async def test_consultar_data_future_relative_em_n_dias():
    from app.graph.tools import consultar_data
    now = datetime.now(TZ)
    future = (now + timedelta(days=10)).strftime("%d/%m/%Y")
    result = await consultar_data.coroutine(data=future)
    assert "em 10 dias" in result


async def test_consultar_data_past_explicit_date_ha_n_dias():
    from app.graph.tools import consultar_data
    now = datetime.now(TZ)
    past = (now - timedelta(days=5)).strftime("%d/%m/%Y")
    result = await consultar_data.coroutine(data=past)
    assert "há 5 dias" in result
    assert "atrás" not in result


def _make_supabase_client_with_appointment_waived(booking_fee_waived=True, custom_price=None):
    """Like _make_supabase_client_with_appointment but with booking_fee_waived in the appointment row.
    Call 3 returns custom_price data instead of empty."""
    appts_with_users = MagicMock(data=[{
        "appointment_id": "apt-wv",
        "start_time": "2026-06-15T10:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "status": "scheduled",
        "patients": {"id": "user-123", "name": "Maria"},
    }])
    apt_data = MagicMock(data=[{
        "appointment_id": "apt-wv",
        "start_time": "2026-06-15T10:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "end_time": "2026-06-15T11:00:00+00:00",
        "paid_at": None,
        "booking_fee_paid_at": None,
        "status": "scheduled",
        "consultation_type": None,
        "booking_fee_waived": booking_fee_waived,
    }])
    custom_price_data = MagicMock(data={"custom_price": custom_price})
    empty = MagicMock(data=[])

    def _side_effect(*_a, **_kw):
        _side_effect.call_count += 1
        # Call 1: appts_with_users (patient resolution)
        if _side_effect.call_count == 1:
            return appts_with_users
        # Call 2: scheduled_raw (PRIORITY 1) → found scheduled appointment
        if _side_effect.call_count == 2:
            return apt_data
        # Call 3: custom_price_data
        if _side_effect.call_count == 3:
            return custom_price_data
        return empty
    _side_effect.call_count = 0

    execute = AsyncMock(side_effect=_side_effect)
    table = MagicMock()
    for m in ("select", "eq", "in_", "limit", "single", "maybe_single",
              "gte", "order", "insert", "update", "upsert", "is_"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


async def test_register_payment_booking_fee_waived_no_deduction():
    """When booking_fee_waived=True on the appointment, expected_remaining = expected (no R$100 deduction).
    Dr. Júlio adult June 2026: expected=650. Paying 650 → QUITADA."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment_waived(
        booking_fee_waived=True, custom_price=None
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="650,00",
            drive_link="https://drive.google.com/file/d/abc/view",
            state=_make_state(preferred_doctor="julio", patient_age=35),
            config=CONFIG,
        )
    assert "QUITADA" in result


async def test_register_payment_courtesy_zero_price():
    """When custom_price=0 (courtesy), the tool returns QUITADA immediately."""
    from app.graph.tools import register_payment
    client, table, execute = _make_supabase_client_with_appointment_waived(
        booking_fee_waived=True, custom_price=0
    )
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-123", "patient_name": "Maria"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.google_drive.rename_file", new_callable=AsyncMock), \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock):
        result = await register_payment.coroutine(
            amount="0,00",
            drive_link="",
            state=_make_state(preferred_doctor="julio", patient_age=35),
            config=CONFIG,
        )
    assert "QUITADA" in result
    assert "cortesia" in result.lower()


# ── waive_booking_fee ─────────────────────────────────────────────────────────


async def test_waive_booking_fee_requires_silent_mode():
    """Eva não pode isentar a taxa por conta própria fora de nota privada da atendente."""
    from app.graph.tools import waive_booking_fee
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await waive_booking_fee.coroutine(
            state=_make_state(),
            config=CONFIG,
        )
    assert "INSTRUÇÃO INTERNA" in result
    table.update.assert_not_called()
    mock_log.assert_not_awaited()


async def test_waive_booking_fee_no_user_found():
    from app.graph.tools import waive_booking_fee
    client, table, execute = _make_supabase_client()
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value=None):
        result = await waive_booking_fee.coroutine(
            state=_make_state(silent_mode=True),
            config=CONFIG,
        )
    assert "Não encontrei cadastro" in result


async def test_waive_booking_fee_no_pending_appointment():
    from app.graph.tools import waive_booking_fee
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data=[])
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "patient-1"}):
        result = await waive_booking_fee.coroutine(
            state=_make_state(silent_mode=True),
            config=CONFIG,
        )
    assert "Não encontrei consulta agendada" in result


async def test_waive_booking_fee_updates_appointment_and_logs():
    """Caso principal: isenta a taxa gravando booking_fee_waived no agendamento —
    sem isso, o cancelamento automático por falta de pagamento não reconhece a isenção
    combinada verbalmente (bug reportado: consulta cancelada apesar da taxa isentada)."""
    from app.graph.tools import waive_booking_fee
    client, table, execute = _make_supabase_client()
    future_start = (datetime.now(TZ) + timedelta(days=3)).isoformat()
    execute.side_effect = [
        MagicMock(data=[{"appointment_id": "evt-abc", "start_time": future_start}]),  # select
        MagicMock(data=[]),  # update
    ]
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock, return_value={"id": "patient-1"}), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await waive_booking_fee.coroutine(
            state=_make_state(silent_mode=True),
            config=CONFIG,
        )
    assert "isentada" in result.lower()
    table.update.assert_called_once()
    update_payload = table.update.call_args[0][0]
    assert update_payload["booking_fee_waived"] is True
    assert update_payload["booking_fee_paid_at"] is not None
    mock_log.assert_awaited_once()
    assert mock_log.call_args[0][0] == "booking_fee_waived"


# ── update_patient_ages script logic ─────────────────────────────────────────

def test_age_from_birth_date_dd_mm_yyyy():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    # Birthday already passed this year
    assert _age_from_birth_date("10/03/1990", today) == 36
    # Birthday not yet reached this year
    assert _age_from_birth_date("20/07/1990", today) == 35


def test_age_from_birth_date_iso():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    assert _age_from_birth_date("1990-03-10", today) == 36


def test_age_from_birth_date_exact_birthday():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    assert _age_from_birth_date("18/05/1990", today) == 36  # birthday today → counts


def test_age_from_birth_date_minor():
    from scripts.update_patient_ages import _age_from_birth_date
    from datetime import date
    today = date(2026, 5, 18)
    assert _age_from_birth_date("15/03/2015", today) == 11
    assert _age_from_birth_date("15/03/2015", today) < 18


def test_age_from_birth_date_invalid_returns_none():
    from scripts.update_patient_ages import _age_from_birth_date
    assert _age_from_birth_date("not-a-date") is None
    assert _age_from_birth_date("") is None
    assert _age_from_birth_date(None) is None


async def test_update_patient_ages_only_updates_changed():
    """Script must update only rows where age differs from birth_date calculation."""
    from scripts.update_patient_ages import main
    from datetime import date
    from unittest.mock import AsyncMock, MagicMock, patch

    today = date(2026, 5, 18)

    users = [
        # age already correct — must NOT be updated
        {"id": "u1", "name": "Alice", "patient_name": None, "birth_date": "10/03/1990", "age": 36},
        # age wrong (didn't update last year) — must be updated
        {"id": "u2", "name": "Bob",   "patient_name": None, "birth_date": "10/03/1990", "age": 35},
        # no stored age — must be updated
        {"id": "u3", "name": "Carol", "patient_name": None, "birth_date": "20/07/2015", "age": None},
    ]

    # Build a single chain mock that handles all builder patterns:
    # .select().not_.is_().execute()  AND  .update().eq().execute()
    chain = MagicMock()
    chain.execute = AsyncMock(return_value=MagicMock(data=users))
    chain.not_ = chain        # attribute access (not a call)
    chain.is_.return_value = chain
    chain.eq.return_value = chain

    table = MagicMock()
    table.select.return_value = chain
    # Each .update() call must return a fresh chain with its own execute tracker
    update_execute = AsyncMock(return_value=MagicMock(data=[]))
    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.execute = update_execute
    table.update.return_value = update_chain

    client = MagicMock()
    client.from_.return_value = table

    with patch("scripts.update_patient_ages.date") as mock_date, \
         patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client):
        mock_date.today.return_value = today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await main()

    # update() should have been called exactly twice (u2 and u3)
    assert table.update.call_count == 2
    updated_ids = {call.args[0]["age"] for call in table.update.call_args_list}
    # Both updates set age=36 (u2: corrects stale age; u3: was None → now 10)
    # u3 born 20/07/2015, today 18/05/2026 → age 10
    ages_written = [call.args[0]["age"] for call in table.update.call_args_list]
    assert 36 in ages_written   # u2 corrected
    assert 10 in ages_written   # u3 filled in


# ── save_patient_email ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_patient_email_passes_user_id_from_state():
    """save_patient_email must pass user_id=state['user_db_id'] so upsert_user updates
    the known patient directly, instead of falling back to resolve_active_patient
    (which can silently no-op when the patient_contacts link isn't resolvable yet)."""
    from app.graph.tools import save_patient_email

    state = _make_state(user_db_id="patient-id-1")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await save_patient_email.coroutine(
            email="paciente@email.com",
            state=state,
            config=CONFIG,
        )

    mock_upsert.assert_awaited_once_with(
        PHONE, {"email": "paciente@email.com"}, user_id="patient-id-1"
    )
    assert "paciente@email.com" in result


# ── set_social_name ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_social_name_sanitizes_and_persists():
    from app.graph.tools import set_social_name
    state = _make_state(user_db_id="patient-id-1")
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        result = await set_social_name.coroutine(
            social_name="Malu, 25 anos",
            state=state,
            config=CONFIG,
        )
    mock_upsert.assert_awaited_once_with(PHONE, {"social_name": "Malu"}, user_id="patient-id-1")
    mock_log.assert_awaited_once_with("social_name_set", PHONE, {"social_name": "Malu"})
    assert "Malu" in result


@pytest.mark.asyncio
async def test_set_social_name_rejects_empty_after_sanitization():
    from app.graph.tools import set_social_name
    state = _make_state(user_db_id="patient-id-1")
    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await set_social_name.coroutine(
            social_name="(  )",
            state=state,
            config=CONFIG,
        )
    mock_upsert.assert_not_awaited()
    assert "não entendi" in result.lower()


# ── request_registration_update ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_registration_update_email():
    """For field=email: updates DB AND sends notification email."""
    from app.graph.tools import request_registration_update

    state = _make_state(patient_name="Ana Souza")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="email",
            new_value="ana.novo@email.com",
            state=state,
            config=CONFIG,
        )

    # DB must be updated for email
    mock_upsert.assert_awaited_once()
    assert "ana.novo@email.com" in str(mock_upsert.call_args)

    # Notification must be sent
    mock_notify.assert_awaited_once()
    notify_call_str = str(mock_notify.call_args)
    assert "alteração cadastral" in notify_call_str.lower() or "Ana Souza" in notify_call_str

    assert "email" in result.lower()


@pytest.mark.asyncio
async def test_request_registration_update_other_field():
    """For non-email field: sends notification but does NOT update DB."""
    from app.graph.tools import request_registration_update

    state = _make_state(patient_name="Carlos Lima")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="CPF",
            new_value="123.456.789-00",
            state=state,
            config=CONFIG,
        )

    # DB must NOT be updated for non-email fields
    mock_upsert.assert_not_awaited()

    # Notification must still be sent
    mock_notify.assert_awaited_once()

    assert "CPF" in result or "cpf" in result.lower()


@pytest.mark.asyncio
async def test_request_registration_update_missing_patient_name_applies_directly():
    """When is_patient=False and patient_name is still missing/defaulted to the
    contact's own name, filling it in is not an "edit" of an established value —
    it's collect_info's job that slipped through. Must update the DB immediately
    instead of just queueing a manual review (fixed 2026-07-01, Adriana case)."""
    from app.graph.tools import request_registration_update

    state = _make_state(
        user_name="Adriana de Faria Pilar",
        patient_name="Adriana de Faria Pilar",  # stale default, never a real answer
        is_patient=False,
        user_db_id="patient-id-1",
    )

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="nome do paciente",
            new_value="Francisco Domingues Bruno de Faria",
            state=state,
            config=CONFIG,
        )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs.get("user_id") == "patient-id-1" or "patient-id-1" in str(mock_upsert.call_args)
    assert "Francisco Domingues Bruno de Faria" in str(mock_upsert.call_args)
    mock_notify.assert_awaited_once()
    assert "sucesso" in result.lower()


@pytest.mark.asyncio
async def test_request_registration_update_existing_patient_name_stays_manual():
    """A genuine correction of an ALREADY-confirmed, distinct patient_name must
    still go through manual review — only the stale-default case auto-applies."""
    from app.graph.tools import request_registration_update

    state = _make_state(
        user_name="Thamiris Izidoro",
        patient_name="Ednaldo José Izidoro da Silva",  # already a real, distinct name
        is_patient=False,
        user_db_id="patient-id-2",
    )

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock) as mock_upsert, \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify, \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="nome do paciente",
            new_value="Ednaldo José da Silva",
            state=state,
            config=CONFIG,
        )

    mock_upsert.assert_not_awaited()
    mock_notify.assert_awaited_once()
    assert "equipe" in result.lower()


@pytest.mark.asyncio
async def test_request_registration_update_returns_confirmation():
    """Return value must mention the requested field."""
    from app.graph.tools import request_registration_update

    state = _make_state(patient_name="Beatriz")

    with patch("app.graph.tools.upsert_user", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock):
        result = await request_registration_update.coroutine(
            field="data de nascimento",
            new_value="15/03/1990",
            state=state,
            config=CONFIG,
        )

    assert "data de nascimento" in result.lower() or "data" in result.lower()
    # Bot stays active — no transfer indicator in return value
    assert "transfer" not in result.lower()
    assert "atendente" not in result.lower() or "equipe" in result.lower()


async def test_reschedule_appointment_presencial_restriction_on_online_only_slot():
    """Restrição presencial NÃO pode sobrepor slot online-only no reagendamento — deve continuar online."""
    from app.graph.tools import reschedule_appointment
    client, table, execute = _make_supabase_client()
    appt_data = {
        "start_time": "2026-03-20T09:00:00-03:00",
        "patient_id": "user-1",
        "patients": {"name": "Maria"},
    }
    execute.return_value = MagicMock(data=appt_data)
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock), \
         patch("app.graph.tools.send_text", new_callable=AsyncMock), \
         patch("app.google_calendar.SCHEDULE_EXCEPTIONS", {}), \
         patch("app.google_calendar.get_modality_for_slot", return_value="online"):  # slot is online-only
        result = await reschedule_appointment.coroutine(
            appointment_id="evt-orig",
            new_slot_datetime="2026-03-25T10:00:00",
            slot_duration_minutes=60,
            state=_make_state(modality_restriction="presencial"),  # restriction says presencial
            config=CONFIG,
            modality="presencial",  # LLM passed presencial — online-only slot should win
        )
    assert mock_update.called
    _, kwargs = mock_update.call_args
    assert kwargs.get("modality") == "online"  # online-only slot wins over presencial restriction


# ── send_pending_payments_reminder filter logic ───────────────────────────────

def test_pending_payments_courtesy_filter():
    """Courtesy appointments (patients.custom_price == 0) must be excluded from consulta_pendente."""
    appts = [
        {"appointment_id": "apt-1", "start_time": "2026-06-01T10:00:00+00:00",
         "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
         "booking_fee_paid_at": None, "paid_at": None, "consultation_type": None,
         "patients": {"name": "Ana", "custom_price": None, "patient_contacts": []}},
        {"appointment_id": "apt-2", "start_time": "2026-06-02T10:00:00+00:00",
         "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
         "booking_fee_paid_at": None, "paid_at": None, "consultation_type": None,
         "patients": {"name": "Cortesia", "custom_price": 0, "patient_contacts": []}},
    ]
    consulta_pendente = [
        appt for appt in appts
        if (appt.get("patients") or {}).get("custom_price") != 0
    ]
    assert len(consulta_pendente) == 1
    assert consulta_pendente[0]["appointment_id"] == "apt-1"


def test_pending_payments_patient_and_contact_extraction():
    """_patient_and_contact must read patient name/phone via patients -> patient_contacts -> contacts,
    preferring the is_self contact, since appointments.patient_id no longer joins to `users`."""
    from scripts.send_pending_payments_reminder import _patient_and_contact

    appt_with_guardian = {
        "patients": {
            "name": "Miguel",
            "custom_price": None,
            "patient_contacts": [
                {"is_self": False, "contacts": {"phone": "5581999999999", "name": "Mãe do Miguel"}},
                {"is_self": True, "contacts": {"phone": "5581888888888", "name": "Miguel"}},
            ],
        },
    }
    patient, contact, phone = _patient_and_contact(appt_with_guardian)
    assert patient == "Miguel"
    assert contact == "Miguel"
    assert phone == "5581888888888"

    appt_no_contacts = {"patients": {"name": "Ana", "custom_price": None, "patient_contacts": []}}
    patient, contact, phone = _patient_and_contact(appt_no_contacts)
    assert patient == "Ana"
    assert contact == "—"
    assert phone == "—"


# ── send_doctor_daily_agenda patient name extraction ──────────────────────────

def test_doctor_daily_agenda_reads_patient_name_from_patients_join():
    """appt.get("patients") replaces the stale appt.get("users") join post-refactor."""
    from scripts.send_doctor_daily_agenda import _format_agenda_email

    appts = [{
        "start_time": "2026-06-01T13:00:00-03:00",
        "end_time": "2026-06-01T14:00:00-03:00",
        "modality": "online",
        "paid_at": None,
        "patients": {"name": "Miguel"},
    }]
    _, body = _format_agenda_email("Dr. Júlio", "01/06/2026", appts)
    assert "Miguel" in body
    assert "Paciente" not in body


# ── _notify_clinic: falha de e-mail nunca some em silêncio ────────────────────

@pytest.mark.asyncio
async def test_notify_clinic_logs_event_when_smtp_not_configured():
    """SMTP ausente registra clinic_email_failed em vez de retornar em silêncio.

    Regressão: até 27/07/2026 o e-mail simplesmente não saía e nada indicava isso —
    pagamentos do Arthur Tenório e da Camila Brasileiro ficaram sem notificar a clínica.
    """
    from app.graph.tools import _notify_clinic

    with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASSWORD": "",
                                 "CLINIC_NOTIFY_EMAIL": ""}, clear=False), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        await _notify_clinic("corpo", phone=PHONE, subject="Comprovante recebido — Arthur")

    mock_log.assert_awaited_once()
    event_type, phone, metadata = mock_log.await_args.args
    assert event_type == "clinic_email_failed"
    assert phone == PHONE
    assert metadata["subject"] == "Comprovante recebido — Arthur"
    assert metadata["origin"] == "bot"
    assert "CLINIC_NOTIFY_EMAIL" in metadata["reason"]


@pytest.mark.asyncio
async def test_notify_clinic_logs_event_when_send_raises():
    """Erro de SMTP no envio também vira clinic_email_failed — e não propaga."""
    from app.graph.tools import _notify_clinic

    async def boom(subject, body):
        raise OSError("connection refused")

    with patch("app.email_sender.send_clinic_notification_email", side_effect=boom), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock) as mock_log:
        await _notify_clinic("corpo", phone=PHONE, subject="Pagamento registrado — Ana")

    mock_log.assert_awaited_once()
    event_type, _, metadata = mock_log.await_args.args
    assert event_type == "clinic_email_failed"
    assert "connection refused" in metadata["reason"]


@pytest.mark.asyncio
async def test_notify_clinic_does_not_raise_when_log_event_also_fails():
    """Notificação nunca derruba o fluxo do paciente, mesmo sem conseguir auditar."""
    from app.graph.tools import _notify_clinic

    async def boom(subject, body):
        raise OSError("connection refused")

    with patch("app.email_sender.send_clinic_notification_email", side_effect=boom), \
         patch("app.graph.tools.log_event", side_effect=RuntimeError("db down")):
        await _notify_clinic("corpo", phone=PHONE, subject="qualquer")


# ── change_modality ──────────────────────────────────────────────────────────

def test_change_modality_is_bound_to_the_agent():
    """Caso Maria Cecília (15139085575, 03/08/2026): a paciente avisou que a consulta
    daquele dia seria online, a Eva respondeu "vou registrar" — e nada mudou. A tool
    change_modality existia e o prompt mandava chamá-la, mas ela nunca foi incluída em
    TOOLS, então a LLM não tinha como chamá-la: sobrava só o texto de confirmação."""
    from app.graph.nodes import TOOLS
    from app.graph.tools import change_modality
    assert change_modality in TOOLS, "change_modality não está bound na LLM"


@pytest.mark.asyncio
async def test_change_modality_keeps_the_original_appointment_time():
    """start_time vem do banco em UTC. Usar .replace(tzinfo=TZ) em vez de
    .astimezone(TZ) reescrevia o evento 3h adiante do horário real."""
    from app.graph.tools import change_modality
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-08-03T18:00:00+00:00",   # 15:00 em Recife
        "end_time": "2026-08-03T19:00:00+00:00",
        "patient_id": "user-1",
        "modality": "presencial",
        "patients": {"name": "Maria", "email": "maria@example.com"},
    })
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await change_modality.coroutine(
            appointment_id="evt-abc",
            new_modality="online",
            state=_make_state(),
            config=CONFIG,
        )

    mock_update.assert_awaited_once()
    kwargs = mock_update.await_args.kwargs
    assert kwargs["new_start"].astimezone(TZ).strftime("%H:%M") == "15:00"
    assert kwargs["slot_minutes"] == 60
    assert kwargs["modality"] == "online"
    assert "15:00" in result


@pytest.mark.asyncio
async def test_change_modality_refuses_presencial_on_online_only_slot():
    """O turno marcado como "apenas online" na grade do médico não vira presencial
    só porque o paciente pediu — confirm_appointment já barrava isso."""
    from app.graph.tools import change_modality
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-08-07T17:00:00+00:00",
        "end_time": "2026-08-07T18:00:00+00:00",
        "patient_id": "user-1",
        "modality": "online",
        "patients": {"name": "Maria", "email": "maria@example.com"},
    })
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.google_calendar.get_modality_for_slot", return_value="online"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await change_modality.coroutine(
            appointment_id="evt-abc",
            new_modality="presencial",
            state=_make_state(),
            config=CONFIG,
        )

    mock_update.assert_not_awaited()
    assert "online" in result.lower()


@pytest.mark.asyncio
async def test_change_modality_refuses_when_registration_restricts_modality():
    """modality_restriction do cadastro vale mais que a preferência do momento —
    mesma precedência já aplicada em confirm_appointment e reschedule_appointment."""
    from app.graph.tools import change_modality
    client, table, execute = _make_supabase_client()
    execute.return_value = MagicMock(data={
        "start_time": "2026-08-03T18:00:00+00:00",
        "end_time": "2026-08-03T19:00:00+00:00",
        "patient_id": "user-1",
        "modality": "online",
        "patients": {"name": "Maria", "email": "maria@example.com"},
    })
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.update_event", new_callable=AsyncMock) as mock_update, \
         patch("app.google_calendar.get_modality_for_slot", return_value="escolha"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock, return_value=[{"id": "user-1"}]), \
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock):
        result = await change_modality.coroutine(
            appointment_id="evt-abc",
            new_modality="presencial",
            state=_make_state(modality_restriction="online"),
            config=CONFIG,
        )

    mock_update.assert_not_awaited()
    assert "cadastro" in result.lower()


# ── custo do cruzamento com o Supabase ────────────────────────────────────────
# Cada query ao Supabase custa ~260 ms. Uma varredura de "qualquer dia" consulta
# vários dias e, em "qualquer" turno, três turnos por dia — buscar por chamada
# daria 120 queries (~31 s) para ler sempre os mesmos dados.

async def test_qualquer_dia_busca_o_supabase_uma_vez_so():
    """A varredura semanal faz UMA busca ao Supabase, não uma por dia/turno."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes,
                          doctor_key, **_kw):
        return []  # nada disponível → força a varredura a expandir ao máximo

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.fetch_supabase_busy", new_callable=AsyncMock,
               return_value=[]) as mock_fetch, \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock,
               side_effect=_fake_slots) as mock_slots:
        await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="qualquer",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert mock_slots.await_count > 10, "a varredura deveria ter consultado vários dias"
    assert mock_fetch.await_count == 1, (
        f"esperava 1 busca ao Supabase para a varredura inteira, "
        f"houve {mock_fetch.await_count} para {mock_slots.await_count} consultas de dia/turno"
    )


async def test_varredura_repassa_as_faixas_do_supabase_para_cada_dia():
    """As faixas buscadas uma vez precisam chegar a cada chamada — se ficarem pelo
    caminho, o cruzamento silenciosamente para de valer e o slot fantasma volta."""
    from app.graph.tools import get_available_slots

    faixas = [(datetime(2026, 7, 8, 9, 0, tzinfo=TZ), datetime(2026, 7, 8, 10, 0, tzinfo=TZ), "evt-1")]

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes,
                          doctor_key, **_kw):
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.fetch_supabase_busy", new_callable=AsyncMock,
               return_value=faixas), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock,
               side_effect=_fake_slots) as mock_slots:
        await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert mock_slots.call_args_list
    assert all(c.kwargs.get("supabase_busy") == faixas for c in mock_slots.call_args_list)


async def test_falha_no_prefetch_nao_derruba_a_busca_de_horarios():
    """Fail-open: se o Supabase cair, a varredura segue com os dados do Calendar."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes,
                          doctor_key, **_kw):
        if preferred_shift == "manha" and preferred_day in ("2026-07-07", "2026-07-08"):
            day = int(preferred_day[-2:])
            return [(datetime(2026, 7, day, 9, 0, tzinfo=TZ), "escolha")]
        return []

    with patch("app.graph.tools.datetime", _FrozenDTTuesday), \
         patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.fetch_supabase_busy", new_callable=AsyncMock,
               side_effect=RuntimeError("supabase fora do ar")), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock,
               side_effect=_fake_slots):
        result = await get_available_slots.coroutine(
            preferred_day="qualquer dia",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )

    assert "07/07" in result
    assert "08/07" in result
# ── Match de paciente por nome em telefone compartilhado ─────────────────────
# A busca por nome desempata qual paciente de um contato está sendo agendado.
# Ela roda em três passadas (nome civil exato, nome social exato, substring) e
# desde o #131 vale também para o guard de consulta duplicada, não só para o
# insert — então um match errado passou a poder bloquear ou liberar o irmão
# errado. São 23 contatos com mais de um paciente (famílias que dividem um
# telefone), incluindo o caso Daniela/Silvia Passos (5581981179458, 3 pacientes).

def test_match_por_nome_respeita_limite_de_palavra():
    """"Ana" casava com "Mariana Silva": a busca por substring não olhava limite
    de palavra e anexava a consulta ao irmão errado."""
    from app.graph.tools import _match_patient_by_name

    pacientes = [
        {"id": "p1", "patient_name": "Mariana Silva"},
        {"id": "p2", "patient_name": "Ana Souza"},
    ]

    assert _match_patient_by_name(pacientes, "Ana")["id"] == "p2"


def test_match_por_nome_nao_escolhe_pela_ordem_da_lista():
    """Com três irmãs "Maria ...", o alvo "Maria" casava com todas e a escolhida
    era só a primeira que o Supabase devolvesse. Sem critério para decidir, a
    busca desiste em vez de chutar."""
    from app.graph.tools import _match_patient_by_name

    irmas = [
        {"id": "p1", "patient_name": "Maria Clara Passos"},
        {"id": "p2", "patient_name": "Maria Eduarda Passos"},
        {"id": "p3", "patient_name": "Maria Luiza Passos"},
    ]

    assert _match_patient_by_name(irmas, "Maria") is None


def test_match_por_nome_ainda_casa_quando_so_uma_irma_bate():
    """O aperto não pode quebrar o caso normal: substring que identifica UMA
    paciente continua valendo."""
    from app.graph.tools import _match_patient_by_name

    irmas = [
        {"id": "p1", "patient_name": "Maria Clara Passos"},
        {"id": "p2", "patient_name": "Joana Eduarda Passos"},
    ]

    assert _match_patient_by_name(irmas, "Clara")["id"] == "p1"


def test_match_por_nome_exato_vence_ambiguidade_de_substring():
    """Nome civil exato é a primeira passada — a ambiguidade da terceira não
    pode atrapalhar quem já foi identificado com precisão."""
    from app.graph.tools import _match_patient_by_name

    pacientes = [
        {"id": "p1", "patient_name": "Maria"},
        {"id": "p2", "patient_name": "Maria Eduarda"},
    ]

    assert _match_patient_by_name(pacientes, "Maria")["id"] == "p1"


def test_match_por_nome_lida_com_acento():
    from app.graph.tools import _match_patient_by_name

    pacientes = [
        {"id": "p1", "patient_name": "Luciana Araújo"},
        {"id": "p2", "patient_name": "Ana Araújo"},
    ]

    assert _match_patient_by_name(pacientes, "Ana")["id"] == "p2"


async def test_resolve_patient_com_varios_pacientes_e_nada_identificando_prefere_o_ativo():
    """Caminho que ficou descoberto no #131: contato com vários pacientes, sem
    override da atendente, com user_db_id órfão e nome que não identifica ninguém.
    Não dá para saber qual irmã é — mas guard e insert usam o MESMO resolvedor,
    então pelo menos concordam, e a escolha cai numa regra definida (o paciente
    ativo) em vez da ordem em que o Supabase respondeu."""
    from app.graph.tools import _resolve_patient_for_booking
    _clara = {"id": "clara-id", "patient_name": "Maria Clara Passos", "active": False}
    _duda = {"id": "duda-id", "patient_name": "Maria Eduarda Passos", "active": True}

    with patch("app.graph.tools.get_users_by_phone", new_callable=AsyncMock,
               return_value=[_clara, _duda]), \
         patch("app.graph.tools.get_user_by_phone", new_callable=AsyncMock,
               return_value=_duda):
        user = await _resolve_patient_for_booking(
            "5581981179458",
            {"user_db_id": "id-fantasma", "patient_name": "Maria"},
        )

    assert user["id"] == "duda-id"


# ── Guard: comprovante PIX para chave que não é da clínica ───────────────────
from app.graph.tools import _receipt_destination_is_foreign


def test_foreign_phone_key_is_flagged():
    # Caso real João Pedro: PIX para a própria chave-telefone, não para o CNPJ.
    desc = ("COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, "
            "chave PIX +55 81 99242 4522, nome do destinatário José Reinaldo da Costa "
            "Gomes Filho, data/hora da transação 18/08/2026 - 11:00:07.")
    assert _receipt_destination_is_foreign(desc) is True


def test_clinic_cnpj_with_punctuation_passes():
    desc = ("COMPROVANTE DE PAGAMENTO: valor R$ 100,00, "
            "chave PIX 42.006.848/0001-78, nome do destinatário PSIQUE, 18 AGO 2026.")
    assert _receipt_destination_is_foreign(desc) is False


def test_clinic_cnpj_plain_digits_passes():
    desc = ("COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX 42006848000178, "
            "destinatário PSIQUE.")
    assert _receipt_destination_is_foreign(desc) is False


def test_masked_key_without_foreign_key_passes():
    # Máscara curta, sem chave estrangeira legível → fail-open.
    desc = "COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX ***.848/1-78, PSIQUE."
    assert _receipt_destination_is_foreign(desc) is False


def test_empty_description_passes():
    assert _receipt_destination_is_foreign("") is False


def test_third_party_cpf_is_flagged():
    desc = ("COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX 123.456.789-00, "
            "nome do destinatário Fulano de Tal, 18/08/2026.")
    assert _receipt_destination_is_foreign(desc) is True


@pytest.mark.asyncio
async def test_register_payment_blocks_foreign_key_no_side_effects():
    from app.graph.tools import register_payment

    desc = ("COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, "
            "chave PIX +55 81 99242 4522, nome do destinatário José Reinaldo, "
            "18/08/2026 - 11:00:07.")
    state = {"messages": [], "preferred_doctor": "julio"}
    config = {"configurable": {"phone": "5581992424522"}}

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock) as mock_db, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheet:
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/ABC/view",
            state=state,
            config=config,
            image_description=desc,
        )

    assert "42006848000178" in result
    assert "outra chave" in result.lower()
    mock_db.assert_not_called()       # rejeitou antes de tocar o Supabase
    mock_sheet.assert_not_called()    # nada gravado na planilha


@pytest.mark.asyncio
async def test_register_payment_clinic_key_passes_guard():
    # Comprovante com a chave da clínica NÃO é barrado: a tool avança além do guard.
    from app.graph.tools import register_payment

    desc = ("COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX 42.006.848/0001-78, "
            "nome do destinatário PSIQUE, 18/08/2026.")
    state = {"messages": [], "preferred_doctor": "julio"}
    config = {"configurable": {"phone": "5581999999999"}}
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock,
               side_effect=RuntimeError("reached get_supabase")) as mock_db:
        with pytest.raises(RuntimeError, match="reached get_supabase"):
            await register_payment.coroutine(
                amount="100,00", drive_link="", state=state, config=config,
                image_description=desc,
            )
    mock_db.assert_called_once()  # passou pelo guard


@pytest.mark.asyncio
async def test_register_payment_panel_skips_guard_even_with_foreign_desc():
    # Pagamento do painel (is_link=True) ignora o guard mesmo com desc de chave estrangeira.
    from app.graph.tools import register_payment

    desc = ("COMPROVANTE DE PAGAMENTO: chave PIX +55 81 99242 4522, "
            "nome do destinatário José Reinaldo.")
    state = {"messages": [], "preferred_doctor": "julio"}
    config = {"configurable": {"phone": "5581999999999"}}
    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock,
               side_effect=RuntimeError("reached get_supabase")) as mock_db:
        with pytest.raises(RuntimeError, match="reached get_supabase"):
            await register_payment.coroutine(
                amount="100,00", drive_link="", state=state, config=config,
                image_description=desc, is_link=True,
            )
    mock_db.assert_called_once()  # guard pulado, avançou
