"""Tests for dashboard/attendant_db.py — whitelist validation and data filtering."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
from attendant_db import _PATIENT_FIELDS, _CONTACT_FIELDS, _LINK_FIELDS


def test_social_name_in_patient_fields_whitelist():
    """Verify that 'social_name' is in _PATIENT_FIELDS whitelist.

    Allows dashboard attendants to manually edit patient social_name via the form.
    """
    assert "social_name" in _PATIENT_FIELDS


def test_patient_fields_whitelist_contains_core_fields():
    """Verify that core patient fields are in the whitelist."""
    expected = {"name", "birth_date", "age", "patient_cpf", "email", "doctor_id",
                "is_returning_patient", "modality_restriction", "age_exception",
                "custom_price", "financial_name", "financial_cpf", "financial_email"}
    assert expected.issubset(_PATIENT_FIELDS)


def test_contact_fields_whitelist_is_valid():
    """Verify that _CONTACT_FIELDS is a non-empty set."""
    assert isinstance(_CONTACT_FIELDS, set)
    assert len(_CONTACT_FIELDS) > 0
    assert "name" in _CONTACT_FIELDS
    assert "cpf" in _CONTACT_FIELDS


def test_link_fields_whitelist_is_valid():
    """Verify that _LINK_FIELDS is a non-empty set."""
    assert isinstance(_LINK_FIELDS, set)
    assert len(_LINK_FIELDS) > 0
    assert "is_self" in _LINK_FIELDS
    assert "relationship" in _LINK_FIELDS
