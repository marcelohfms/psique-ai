"""Testa a lógica pura de abandono do audit de agendamento não finalizado."""
from datetime import datetime, timedelta, timezone

from scripts._audit_slots_offered_no_booking import select_abandoned

NOW = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=4)  # ofertas anteriores a isto já venceram a janela


def _offer(dt: datetime, **md) -> dict:
    return {"created_at": dt.isoformat(), "metadata": md}


def test_offer_older_than_cutoff_without_booking_is_abandoned():
    latest = {"5583111": _offer(NOW - timedelta(hours=5), doctor="julio")}
    result = select_abandoned(latest, {}, CUTOFF)
    assert [c["phone"] for c in result] == ["5583111"]
    assert result[0]["metadata"]["doctor"] == "julio"


def test_offer_within_window_is_not_abandoned():
    latest = {"5583111": _offer(NOW - timedelta(hours=1))}
    assert select_abandoned(latest, {}, CUTOFF) == []


def test_booking_after_offer_removes_case():
    offered = NOW - timedelta(hours=5)
    latest = {"5583111": _offer(offered)}
    booked = {"5583111": [offered + timedelta(minutes=10)]}
    assert select_abandoned(latest, booked, CUTOFF) == []


def test_booking_before_offer_still_abandoned():
    """Confirmou uma consulta antiga, depois pediu datas de novo e parou:
    a confirmação anterior à última oferta não conta como conversão."""
    offered = NOW - timedelta(hours=5)
    latest = {"5583111": _offer(offered)}
    booked = {"5583111": [offered - timedelta(days=30)]}
    result = select_abandoned(latest, booked, CUTOFF)
    assert [c["phone"] for c in result] == ["5583111"]


def test_multiple_phones_sorted_by_offer_time():
    latest = {
        "A": _offer(NOW - timedelta(hours=5)),
        "B": _offer(NOW - timedelta(hours=8)),
        "C": _offer(NOW - timedelta(hours=1)),   # dentro da janela → excluído
    }
    result = select_abandoned(latest, {}, CUTOFF)
    assert [c["phone"] for c in result] == ["B", "A"]
