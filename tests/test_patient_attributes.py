"""Testa a lógica pura dos custom attributes do paciente (app/patient_attributes.py)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.patient_attributes import (
    pick_next_appointment, fee_status, format_next_appointment,
    build_attributes, needs_attr_change,
    ATTR_EVENT, ATTR_KEYS,
)

TZ = ZoneInfo("America/Recife")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _appt(start, **over):
    a = {"status": "scheduled", "start_time": start.isoformat(),
         "modality": "online", "patient_id": "p1",
         "booking_fee_paid_at": None, "booking_fee_waived": False}
    a.update(over)
    return a


# ── pick_next_appointment ─────────────────────────────────────────────────────

def test_pick_next_returns_soonest_future_scheduled():
    a1 = _appt(NOW + timedelta(days=2))
    a2 = _appt(NOW + timedelta(days=1))
    a3 = _appt(NOW - timedelta(days=1))          # passada
    a4 = _appt(NOW + timedelta(days=3), status="completed")  # não scheduled
    got = pick_next_appointment([a1, a2, a3, a4], NOW)
    assert got is a2


def test_pick_next_none_when_no_future():
    a = _appt(NOW - timedelta(hours=1))
    assert pick_next_appointment([a], NOW) is None


# ── fee_status ────────────────────────────────────────────────────────────────

def test_fee_status_waived():
    assert fee_status(_appt(NOW, booking_fee_waived=True), None) == "Isenta"


def test_fee_status_courtesy_price_zero():
    assert fee_status(_appt(NOW), 0) == "Isenta"


def test_fee_status_paid():
    assert fee_status(_appt(NOW, booking_fee_paid_at="2026-09-19T10:00:00Z"), 200) == "Paga"


def test_fee_status_pending():
    assert fee_status(_appt(NOW), 200) == "Pendente"


# ── format_next_appointment ───────────────────────────────────────────────────

def test_format_next_appointment_has_date_time_modality_name():
    # 14:00 UTC = 11:00 em Recife (UTC-3)
    appt = _appt(datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc), modality="presencial")
    got = format_next_appointment(appt, "João Silva", TZ)
    assert got == "22/09/2026 14:00 presencial — João"


# ── build_attributes ──────────────────────────────────────────────────────────

def test_build_attributes_with_next_appointment():
    appt = _appt(datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc))
    attrs = build_attributes(
        doctor_label="Dr. Júlio", next_appt=appt, patient_name="João Silva",
        is_returning=True, custom_price=200, tz=TZ,
    )
    assert attrs == {
        "medico": "Dr. Júlio",
        "proxima_consulta": "22/09/2026 14:00 online — João",
        "taxa_reserva": "Pendente",
        "retornante": "Retornante",
    }
    assert set(attrs) == set(ATTR_KEYS)


def test_build_attributes_without_next_appointment():
    attrs = build_attributes(
        doctor_label="Dra. Bruna", next_appt=None, patient_name="Ana",
        is_returning=False, custom_price=None, tz=TZ,
    )
    assert attrs["proxima_consulta"] == "sem consulta futura"
    assert attrs["taxa_reserva"] == ""
    assert attrs["medico"] == "Dra. Bruna"
    assert attrs["retornante"] == "Primeira vez"


# ── needs_attr_change ─────────────────────────────────────────────────────────

def test_needs_attr_change():
    a = {"medico": "Dr. Júlio"}
    assert needs_attr_change(a, None) is True
    assert needs_attr_change(a, {"medico": "Dr. Júlio"}) is False
    assert needs_attr_change(a, {"medico": "Dra. Bruna"}) is True


def test_attr_event_name():
    assert ATTR_EVENT == "contact_attributes_synced"
