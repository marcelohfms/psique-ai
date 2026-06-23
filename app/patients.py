"""Camada de dados nativa para patients / contacts / patient_contacts.

Substitui gradualmente o modelo antigo de `users` (ver app/database.py).
"""
from datetime import datetime, timezone

from app.database import get_supabase


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


def normalize_phone(phone: str) -> str:
    """Retorna a forma canônica do número de celular brasileiro (com o 9)."""
    digits = _strip_phone(phone)
    if len(digits) == 13 and digits.startswith("55"):
        return digits
    if len(digits) == 12 and digits.startswith("55"):
        return digits[:4] + "9" + digits[4:]
    return digits


async def get_contact_by_phone(phone: str) -> dict | None:
    """Retorna a linha de `contacts` para este número (forma canônica), ou None."""
    client = await get_supabase()
    canonical = normalize_phone(phone)
    result = (
        await client.from_("contacts")
        .select("*")
        .eq("phone", canonical)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None
