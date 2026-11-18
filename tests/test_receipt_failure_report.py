"""Testes da lógica pura do relatório diário de falhas de leitura de comprovante."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.receipt_failure_report import (
    build_report, format_report, REPORT_EVENT_TYPES, EXCLUDED_PHONES_RAW,
)
from app.receipt_reconciliation import RECEIPT_DROP_EVENT, REPLAY_EVENT

TZ = ZoneInfo("America/Recife")
NOW = datetime(2026, 11, 19, 8, 0, tzinfo=TZ)
UNREADABLE = "payment_receipt_unreadable"


def _ev(et, phone, created_at, attachment_id=None):
    meta = {}
    if attachment_id is not None:
        meta["attachment_id"] = attachment_id
    return {"event_type": et, "phone": phone, "created_at": created_at, "metadata": meta}


def _name(mapping):
    return lambda p: mapping.get(p, "(sem nome)")


def test_report_event_types_cover_the_three_signals():
    assert RECEIPT_DROP_EVENT in REPORT_EVENT_TYPES
    assert REPLAY_EVENT in REPORT_EVENT_TYPES
    assert UNREADABLE in REPORT_EVENT_TYPES


def test_empty_events_produce_empty_report():
    r = build_report([], now=NOW, tz=TZ, name_of=_name({}))
    assert r["por_dia"] == []
    assert r["totais"] == {"caiu": 0, "resgatado": 0, "ilegivel": 0, "pendentes": 0}


def test_affectus_number_is_excluded_even_with_the_extra_9():
    # O Chatwoot é compartilhado com outro projeto (Clínica Affectus). Esses anexos
    # não são da Psiquê e não podem poluir o relatório — nas duas variantes do número.
    events = [
        _ev(RECEIPT_DROP_EVENT, "558191812399", "2026-11-18T12:00:00+00:00", attachment_id=1),
        _ev(REPLAY_EVENT, "5581991812399", "2026-11-18T13:00:00+00:00", attachment_id=1),
    ]
    r = build_report(events, now=NOW, tz=TZ, name_of=_name({}))
    assert r["por_dia"] == []
    assert r["totais"]["caiu"] == 0
    assert r["n_excluidos"] == 2


def test_dropped_without_replay_is_flagged_pending():
    events = [
        _ev(RECEIPT_DROP_EVENT, "5581999736464", "2026-11-18T12:00:00+00:00", attachment_id=10),
        _ev(RECEIPT_DROP_EVENT, "5581999736464", "2026-11-18T13:00:00+00:00", attachment_id=11),
        _ev(REPLAY_EVENT, "5581999736464", "2026-11-18T13:30:00+00:00", attachment_id=11),
    ]
    r = build_report(events, now=NOW, tz=TZ, name_of=_name({"5581999736464": "Fulana"}))
    assert r["totais"]["caiu"] == 2
    assert r["totais"]["resgatado"] == 1
    assert r["totais"]["pendentes"] == 1  # só o anexo 10 ficou sem resgate
    itens = [i for dia in r["por_dia"] for i in dia["itens"]]
    por_anexo = {i["attachment_id"]: i for i in itens if i.get("attachment_id")}
    assert por_anexo[10]["pendente"] is True
    assert por_anexo[11]["pendente"] is False


def test_illegible_is_counted_and_listed():
    events = [_ev(UNREADABLE, "5581988197076", "2026-11-18T15:25:00+00:00")]
    r = build_report(events, now=NOW, tz=TZ, name_of=_name({"5581988197076": "Marina"}))
    assert r["totais"]["ilegivel"] == 1
    itens = [i for dia in r["por_dia"] for i in dia["itens"]]
    assert any(i["nome"] == "Marina" and i["categoria"] == "ilegível" for i in itens)


def test_format_report_has_date_and_patient_name():
    events = [_ev(RECEIPT_DROP_EVENT, "353833450100", "2026-11-18T18:36:00+00:00", attachment_id=2229)]
    r = build_report(events, now=NOW, tz=TZ, name_of=_name({"353833450100": "Júlia"}))
    subject, body = format_report(r, now=NOW, tz=TZ)
    assert "18/11/2026" in body
    assert "Júlia" in body
    assert "comprovante" in subject.lower()
