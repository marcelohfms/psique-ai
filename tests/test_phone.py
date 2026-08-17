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
