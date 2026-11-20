"""Helpers de número de telefone brasileiro.

Módulo-folha: NÃO importa nada de `app.*`. Existe para que `app/database.py` e
`app/patients.py` compartilhem estes helpers sem criar um ciclo de imports.
Ver docs/superpowers/specs/2026-08-14-quebrar-import-circular-patients-database-design.md
"""


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


# Números TOTALMENTE bloqueados: nenhuma mensagem deles é processada pela Eva —
# nem webhook, nem cron, nem e-mail, nem gravação. Diferente do manual_hold, que
# silencia a Eva no recebimento mas ainda reinjeta comprovante perdido e avisa a
# clínica por e-mail. Use a forma canônica COM o 9 (55 + DDD + 9XXXXXXXX).
# Ver docs/superpowers/specs/2026-11-19-blocklist-numero-total-design.md
#   5581991812399 — WhatsApp da Clínica Affectus, manda comprovante de terceiros
#                   como imagem sem legenda; não é paciente (19/11/2026).
BLOCKED_NUMBERS = frozenset({
    "5581991812399",
})


def is_blocked_number(phone: str) -> bool:
    """True se o número está na blocklist total, em qualquer variante: com ou sem o
    9, com ou sem `+` ou sufixo `@s.whatsapp.net`. Função pura, usável em todos os
    canos de entrada (webhook Meta, webhook Chatwoot, cron, process_message)."""
    if not phone:
        return False
    cleaned = _strip_phone(phone).lstrip("+").strip()
    return any(v in BLOCKED_NUMBERS for v in _phone_variants(cleaned))


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
