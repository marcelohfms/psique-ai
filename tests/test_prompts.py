from app.graph.prompts import (
    EXISTING_PATIENT_SYSTEM,
    NEW_PATIENT_SYSTEM,
    get_booking_fee_rule,
)


def test_booking_rule_has_managed_patient_rule():
    # A regra do agendamento é injetada nos dois prompts via {booking_fee_rule}.
    assert "{booking_fee_rule}" in EXISTING_PATIENT_SYSTEM
    assert "{booking_fee_rule}" in NEW_PATIENT_SYSTEM
    text = get_booking_fee_rule()
    assert "register_managed_patient" in text
    assert "menor" in text.lower()
    assert "patient_name_override" in text
