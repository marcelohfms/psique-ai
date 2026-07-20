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
              "gte", "order", "insert", "update", "upsert", "or_", "filter"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table, execute


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

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
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

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
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


async def test_get_available_slots_qualquer_dia_keeps_expanding_until_found():
    """Duas semanas totalmente vazias NUNCA devem gerar mensagem de 'não encontrei' —
    a busca deve continuar expandindo até achar algo."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
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


async def test_get_available_slots_qualquer_dia_e_qualquer_turno_shows_per_shift_breakdown():
    """'qualquer dia' combinado com turno 'qualquer' (o caso real mais comum, já
    que a Eva pergunta o dia antes do turno) deve mostrar o detalhamento por turno."""
    from app.graph.tools import get_available_slots

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
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


async def test_pick_doctor_by_earliest_availability_picks_earlier_doctor():
    """'qualquer um' entre dois médicos válidos → escolhe o de agenda mais próxima.
    Bruna tem vaga na terça (07/07); Júlio só na quinta (09/07) → retorna 'bruna'."""
    from app.graph.tools import pick_doctor_by_earliest_availability

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
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

    async def _fake_slots(*, calendar_id, preferred_day, preferred_shift, slot_minutes, doctor_key):
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


async def test_get_available_slots_semana_que_vem_still_asks_clarification():
    """Regressão: separar 'qualquer'/'tanto faz' de _vague_patterns não pode quebrar
    o fluxo de esclarecimento para 'semana que vem' (sem dia informado)."""
    from app.graph.tools import get_available_slots
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.google_calendar.get_available_slots", new_callable=AsyncMock) as mock_slots:
        result = await get_available_slots.coroutine(
            preferred_day="semana que vem",
            preferred_shift="manha",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "CLARIFICAÇÃO NECESSÁRIA" in result
    mock_slots.assert_not_called()


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
    paciente já está em pending_reschedule (não só 'scheduled'). Caso contrário,
    confirm_appointment escapa da checagem e cria uma linha nova em vez de deixar
    reschedule_appointment atualizar a consulta existente — perdendo a taxa de reserva
    já paga (caso Tiago Perrelli, 03/07/2026)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    execute.side_effect = [
        MagicMock(data=[{"id": "contact-1"}]),                            # contacts select
        MagicMock(data=[{"patient_id": "patient-1"}]),                    # patient_contacts select
        MagicMock(data=[{"appointment_id": "old-evt-1",
                          "start_time": "2026-03-25T12:00:00+00:00"}]),   # appointments guard 0
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-03-23T09:00:00",
            slot_duration_minutes=60,
            state=_make_state(),
            config=CONFIG,
        )
    assert "NÃO crie um novo agendamento" in result
    assert "mark_reschedule_in_progress" in result
    mock_create.assert_not_called()
    _status_call = next(c for c in table.in_.call_args_list if c.args[0] == "status")
    assert set(_status_call.args[1]) == {"scheduled", "pending_reschedule"}


async def test_confirm_appointment_guard0_applies_even_with_force_encaixe():
    """force_encaixe deve pular apenas os guards de janela/conflito de agenda, nunca o
    Guard 0 (paciente já tem consulta futura). Caso contrário, uma atendente pedindo para
    'encaixar' um novo horário faz a Eva criar um segundo agendamento em vez de remarcar
    o existente (caso Gustavo Lapenda, 06/07/2026 — dois agendamentos ativos)."""
    from app.graph.tools import confirm_appointment
    client, table, execute = _make_supabase_client()
    execute.side_effect = [
        MagicMock(data=[{"id": "contact-1"}]),                            # contacts select
        MagicMock(data=[{"patient_id": "patient-1"}]),                    # patient_contacts select
        MagicMock(data=[{"appointment_id": "old-evt-1",
                          "start_time": "2026-07-15T18:00:00+00:00"}]),   # appointments guard 0
    ]
    with patch("app.graph.tools._get_doctor_calendar_id", new_callable=AsyncMock, return_value="cal123"), \
         patch("app.graph.tools.get_supabase", new_callable=AsyncMock, return_value=client), \
         patch("app.google_calendar.create_event", new_callable=AsyncMock) as mock_create:
        result = await confirm_appointment.coroutine(
            slot_datetime="2026-07-08T13:20:00",
            slot_duration_minutes=60,
            state=_make_state(silent_mode=True),
            config=CONFIG,
            force_encaixe=True,
        )
    assert "NÃO crie um novo agendamento" in result
    assert "mark_reschedule_in_progress" in result
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
    assert "confirmada" in result.lower()  # resposta amigável igual
    table.update.assert_not_called()       # NÃO regravou confirmed_at
    mock_log.assert_not_awaited()          # NÃO logou de novo


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
         patch("app.graph.tools.log_event", new_callable=AsyncMock), \
         patch("app.graph.tools._notify_clinic", new_callable=AsyncMock) as mock_notify:
        result = await cancel_appointment.coroutine(
            appointment_id="evt-abc",
            state=_make_state(),
            config=CONFIG,
        )
    assert "cancelada" in result.lower()
    mock_cancel.assert_awaited_once_with("cal123", "evt-abc")
    mock_notify.assert_called()


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

def _make_supabase_client_with_appointment():
    """Supabase client that serves register_payment's two sequential appointment queries.

    Call order:
      1. appts_result — appointments joined with users (patient resolution)
      2. appt_result  — full appointment details (payment logic)
      3+. update/upsert/linked-appts → generic empty response
    """
    # Call 1: new appointment-centric query with users join
    appts_with_users = MagicMock(data=[{
        "appointment_id": "apt-1",
        "start_time": "2026-03-23T09:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "status": "scheduled",
        "patients": {"id": "user-123", "name": "Maria"},
    }])
    # Call 2: full appointment fetch for payment logic
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
