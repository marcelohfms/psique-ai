"""Tests for app/lost_message_recovery.py — varredura de mensagem perdida no buffer."""
from datetime import datetime, timezone

from app.lost_message_recovery import pick_candidates


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
