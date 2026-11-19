from app.phone import _phone_variants, _strip_phone


def test_strip_phone_removes_whatsapp_suffix():
    assert _strip_phone("5581999998888@s.whatsapp.net") == "5581999998888"


def test_strip_phone_leaves_plain_number_untouched():
    assert _strip_phone("5581999998888") == "5581999998888"


def test_phone_variants_13_digits_returns_with_and_without_9():
    assert _phone_variants("5581999998888") == ["5581999998888", "558199998888"]


def test_phone_variants_12_digits_returns_canonical_first():
    assert _phone_variants("558199998888") == ["5581999998888", "558199998888"]


def test_phone_variants_strips_suffix_before_varying():
    assert _phone_variants("5581999998888@s.whatsapp.net") == [
        "5581999998888",
        "558199998888",
    ]


def test_phone_variants_non_brazilian_returns_single():
    assert _phone_variants("12025550123") == ["12025550123"]


def test_database_usa_o_strip_phone_compartilhado():
    """database.py chama _strip_phone 3x (linhas 313, 553, 568).

    Não testamos _phone_variants aqui: em database.py ele é re-export
    temporário, eliminado na Task 5.
    """
    from app import database, phone

    assert database._strip_phone is phone._strip_phone


# ── Blocklist total (is_blocked_number) ───────────────────────────────────────

def test_is_blocked_number_matches_canonical_form():
    from app.phone import is_blocked_number
    assert is_blocked_number("5581991812399") is True


def test_is_blocked_number_matches_12_digit_without_ninth():
    """Meta entrega o número da Affectus sem o 9 (558191812399)."""
    from app.phone import is_blocked_number
    assert is_blocked_number("558191812399") is True


def test_is_blocked_number_matches_with_plus_and_suffix():
    from app.phone import is_blocked_number
    assert is_blocked_number("+5581991812399") is True
    assert is_blocked_number("5581991812399@s.whatsapp.net") is True
    assert is_blocked_number("558191812399@s.whatsapp.net") is True


def test_is_blocked_number_false_for_legitimate_number():
    from app.phone import is_blocked_number
    assert is_blocked_number("5583999999999") is False
    assert is_blocked_number("5581991812398@s.whatsapp.net") is False


def test_is_blocked_number_handles_empty_and_junk():
    from app.phone import is_blocked_number
    assert is_blocked_number("") is False
    assert is_blocked_number(None) is False
