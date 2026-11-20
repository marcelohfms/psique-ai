"""Tests for app/lost_message_recovery.py — varredura de mensagem perdida no buffer."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.lost_message_recovery import pick_candidates, sweep


def _chainable(data):
    """Mock encadeável do Supabase cujo .execute() devolve MagicMock(data=data)."""
    table = MagicMock()
    for m in ("select", "eq", "gte", "lte", "order", "limit"):
        getattr(table, m).return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=data))
    return table


def _client(messages_data, events_data):
    """Cliente cujo from_('messages') e from_('events') devolvem dados distintos."""
    msgs = _chainable(messages_data)
    evs = _chainable(events_data)
    client = MagicMock()
    client.from_.side_effect = lambda t: msgs if t == "messages" else evs
    return client


def test_pick_candidates_user_last_and_old_enough():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [
        {"phone": "551100000001", "role": "user", "content": "sim",
         "created_at": "2026-11-19T14:50:00+00:00"},  # 10 min atrás → candidato
    ]
    out = pick_candidates(rows, now, floor_min=5)
    assert out == [{"phone": "551100000001", "content": "sim",
                    "created_at": "2026-11-19T14:50:00+00:00"}]


def test_pick_candidates_excludes_recent_within_floor():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [{"phone": "551100000001", "role": "user", "content": "sim",
             "created_at": "2026-11-19T14:58:00+00:00"}]  # 2 min → ainda no piso
    assert pick_candidates(rows, now, floor_min=5) == []


def test_pick_candidates_excludes_when_last_is_assistant_or_attendant():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    base = "2026-11-19T14:50:00+00:00"
    later = "2026-11-19T14:51:00+00:00"
    for last_role in ("assistant", "attendant"):
        rows = [
            {"phone": "551100000001", "role": "user", "content": "sim", "created_at": base},
            {"phone": "551100000001", "role": last_role, "content": "ok", "created_at": later},
        ]
        assert pick_candidates(rows, now, floor_min=5) == []


def test_pick_candidates_skips_empty_content():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [{"phone": "551100000001", "role": "user", "content": "",
             "created_at": "2026-11-19T14:50:00+00:00"}]
    assert pick_candidates(rows, now, floor_min=5) == []


def test_pick_candidates_multiple_phones_independent():
    now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    rows = [
        {"phone": "A", "role": "user", "content": "oi", "created_at": "2026-11-19T14:40:00+00:00"},
        {"phone": "A", "role": "assistant", "content": "ola", "created_at": "2026-11-19T14:41:00+00:00"},
        {"phone": "B", "role": "user", "content": "sim", "created_at": "2026-11-19T14:40:00+00:00"},
    ]
    out = pick_candidates(rows, now, floor_min=5)
    assert out == [{"phone": "B", "content": "sim", "created_at": "2026-11-19T14:40:00+00:00"}]


# ── sweep ──────────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
_LOST_ROW = {"phone": "551100000001", "role": "user", "content": "sim",
             "created_at": "2026-11-19T14:50:00+00:00"}


async def test_sweep_reprocesses_lost_message():
    client = _client(messages_data=[_LOST_ROW], events_data=[])
    pm = AsyncMock()
    log = AsyncMock()
    with patch("app.database.get_supabase", AsyncMock(return_value=client)), \
         patch("app.main.process_message", pm), \
         patch("app.database.log_event", log):
        n = await sweep(_NOW)

    assert n == 1
    pm.assert_awaited_once()
    args, kwargs = pm.call_args
    assert args == ("551100000001@s.whatsapp.net", "sim")
    assert kwargs.get("bypass_pause") in (None, False)  # respeita a pausa
    assert log.await_args.args[0] == "lost_message_replayed"


async def test_sweep_skips_already_replayed():
    client = _client(messages_data=[_LOST_ROW], events_data=[{"id": 1}])  # já reprocessado
    pm = AsyncMock()
    log = AsyncMock()
    with patch("app.database.get_supabase", AsyncMock(return_value=client)), \
         patch("app.main.process_message", pm), \
         patch("app.database.log_event", log):
        n = await sweep(_NOW)

    assert n == 0
    pm.assert_not_awaited()
    log.assert_not_awaited()


async def test_sweep_isolates_failing_candidate():
    row2 = {"phone": "551100000002", "role": "user", "content": "oi",
            "created_at": "2026-11-19T14:50:00+00:00"}
    client = _client(messages_data=[_LOST_ROW, row2], events_data=[])
    pm = AsyncMock(side_effect=[RuntimeError("boom"), None])
    log = AsyncMock()
    with patch("app.database.get_supabase", AsyncMock(return_value=client)), \
         patch("app.main.process_message", pm), \
         patch("app.database.log_event", log):
        n = await sweep(_NOW)

    assert pm.await_count == 2        # o 2º ainda roda apesar do 1º estourar
    assert n == 1                     # só o que teve sucesso conta
