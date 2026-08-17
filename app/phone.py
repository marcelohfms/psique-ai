"""Helpers de número de telefone brasileiro.

Módulo-folha: NÃO importa nada de `app.*`. Existe para que `app/database.py` e
`app/patients.py` compartilhem estes helpers sem criar um ciclo de imports.
Ver docs/superpowers/specs/2026-08-14-quebrar-import-circular-patients-database-design.md
"""


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


def _phone_variants(phone: str) -> list[str]:
    """Return both the 9-digit and 8-digit variants of a Brazilian mobile number.

    Brazilian mobiles gained a leading 9 in 2012–2016. Chatwoot/Evolution may
    deliver the same number with or without the extra 9, causing duplicate users.
    We normalise to the WITH-9 form (current standard) and also try the legacy form.
    """
    digits = _strip_phone(phone)
    # Must be a Brazilian mobile: 55 + 2-digit DDD + 8 or 9 digits
    if len(digits) == 13 and digits.startswith("55"):
        # Has the 9 already (55 + DDD + 9XXXXXXXX)
        return [digits, digits[:4] + digits[5:]]   # also try without the 9
    if len(digits) == 12 and digits.startswith("55"):
        # Missing the 9 (55 + DDD + 8XXXXXXXX)
        return [digits[:4] + "9" + digits[4:], digits]  # canonical with-9 first
    return [digits]
