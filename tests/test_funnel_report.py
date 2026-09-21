"""Testa a lógica pura do relatório de funil (app/funnel_report.py)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.funnel_report import (
    etapa_alcancada, compute_funnel, format_report,
    STALL_DAYS,
)

TZ = ZoneInfo("America/Recife")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ── etapa_alcancada ───────────────────────────────────────────────────────────

def test_etapa_paid_is_agendado():
    assert etapa_alcancada("p", qualified=set(), booked=set(), paid={"p"}) == "Agendado"


def test_etapa_booked_unpaid():
    assert etapa_alcancada("p", qualified={"p"}, booked={"p"}, paid=set()) == "Agendou, falta pagar"


def test_etapa_qualified():
    assert etapa_alcancada("p", qualified={"p"}, booked=set(), paid=set()) == "Qualificado"


def test_etapa_interessado():
    assert etapa_alcancada("p", qualified=set(), booked=set(), paid=set()) == "Interessado"


# ── compute_funnel ────────────────────────────────────────────────────────────

def test_compute_funnel_counts_and_buckets():
    cohort = {"a", "b", "c", "d", "e"}
    qualified = {"a", "b", "c", "d"}      # e nunca qualificou
    booked = {"a", "b", "c"}
    paid = {"a", "b"}                      # só a e b converteram
    last_activity = {
        "c": NOW - timedelta(days=1),      # não-convertido, quente → pendente
        "d": NOW - timedelta(days=10),     # não-convertido, frio → perdido
        "e": NOW - timedelta(days=2),      # não-convertido, quente → pendente
    }
    r = compute_funnel(cohort=cohort, qualified=qualified, booked=booked,
                       paid=paid, last_activity=last_activity, now=NOW)
    assert r["interessados"] == 5
    assert r["qualificados"] == 4
    assert r["agendados"] == 2
    assert r["conversao_pct"] == 40
    assert {p["phone"] for p in r["pendentes"]} == {"c", "e"}
    assert r["perdidos"] == 1
    # c chegou até "agendou, falta pagar"; e parou em interessado
    etapas = {p["phone"]: p["etapa"] for p in r["pendentes"]}
    assert etapas["c"] == "Agendou, falta pagar"
    assert etapas["e"] == "Interessado"


def test_compute_funnel_no_activity_is_perdido():
    r = compute_funnel(cohort={"x"}, qualified=set(), booked=set(), paid=set(),
                       last_activity={}, now=NOW)
    assert r["perdidos"] == 1
    assert r["pendentes"] == []


def test_compute_funnel_empty_cohort():
    r = compute_funnel(cohort=set(), qualified=set(), booked=set(), paid=set(),
                       last_activity={}, now=NOW)
    assert r["interessados"] == 0
    assert r["conversao_pct"] == 0


# ── format_report ─────────────────────────────────────────────────────────────

def test_format_report_has_sections_and_names():
    result = {
        "interessados": 5, "qualificados": 4, "agendados": 2, "conversao_pct": 40,
        "pendentes": [{"phone": "5581111", "etapa": "Qualificado", "name": "Ana"},
                      {"phone": "5582222", "etapa": "Interessado", "name": ""}],
        "perdidos": 1,
    }
    subject, body = format_report(result, NOW, TZ)
    assert "Funil de leads" in body
    assert "Interessados" in body and "5" in body
    assert "Conversão: 2 de 5 (40%)" in body
    assert "Ana" in body and "5581111" in body    # pendente com nome
    assert "5582222" in body                        # pendente sem nome cai pro telefone
    assert "2/5" in subject or "2 de 5" in subject
