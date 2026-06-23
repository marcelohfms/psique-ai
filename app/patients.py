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


async def get_patients_by_contact(contact_id: str, role: str | None = None) -> list[dict]:
    """Retorna os pacientes (dicts da tabela patients) vinculados a um contato.

    Quando `role` é informado, filtra pelo papel. Deduplica por id.
    """
    client = await get_supabase()
    query = (
        client.from_("patient_contacts")
        .select("patient_id, role, is_self, patients(*)")
        .eq("contact_id", contact_id)
    )
    if role is not None:
        query = query.eq("role", role)
    result = await query.execute()

    seen: set[str] = set()
    out: list[dict] = []
    for row in (result.data or []):
        patient = row.get("patients")
        if patient and patient["id"] not in seen:
            seen.add(patient["id"])
            out.append(patient)
    return out


async def get_contacts_for_patient(patient_id: str, role: str) -> list[dict]:
    """Retorna os contatos ATIVOS com o papel `role` para um paciente.

    Usado para disparar lembretes/confirmações a todos os responsáveis.
    """
    client = await get_supabase()
    result = (
        await client.from_("patient_contacts")
        .select("contact_id, contacts(*)")
        .eq("patient_id", patient_id)
        .eq("role", role)
        .execute()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for row in (result.data or []):
        contact = row.get("contacts")
        if contact and contact.get("active") and contact["id"] not in seen:
            seen.add(contact["id"])
            out.append(contact)
    return out


async def upsert_contact(phone: str, data: dict) -> str | None:
    """Insere ou atualiza um contato pelo número canônico. Retorna o id."""
    client = await get_supabase()
    canonical = normalize_phone(phone)
    existing = await get_contact_by_phone(canonical)
    if existing:
        await client.from_("contacts").update(data).eq("id", existing["id"]).execute()
        return existing["id"]
    result = await client.from_("contacts").insert({"phone": canonical, **data}).execute()
    inserted = (result.data or [{}])[0]
    return inserted.get("id")


async def upsert_patient(data: dict, patient_id: str | None = None) -> str | None:
    """Insere um paciente novo ou atualiza um existente (por id). Retorna o id."""
    client = await get_supabase()
    if patient_id:
        await client.from_("patients").update(data).eq("id", patient_id).execute()
        return patient_id
    result = await client.from_("patients").insert(data).execute()
    inserted = (result.data or [{}])[0]
    return inserted.get("id")


async def link_patient_contact(
    patient_id: str, contact_id: str, role: str, is_self: bool = False
) -> None:
    """Vincula um contato a um paciente com um papel. Idempotente.

    Usa a constraint UNIQUE(patient_id, contact_id, role).
    """
    client = await get_supabase()
    await client.from_("patient_contacts").upsert(
        {
            "patient_id": patient_id,
            "contact_id": contact_id,
            "role": role,
            "is_self": is_self,
        },
        on_conflict="patient_id,contact_id,role",
    ).execute()
