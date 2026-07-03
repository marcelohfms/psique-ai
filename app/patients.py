"""Camada de dados nativa para patients / contacts / patient_contacts.

Substitui gradualmente o modelo antigo de `users` (ver app/database.py).
"""
from datetime import datetime, timezone

from app.database import get_supabase


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


def normalize_phone(phone: str) -> str:
    """Retorna a forma canônica do número de celular brasileiro (com o 9).

    INVARIANTE DE DESIGN: A tabela `contacts.phone` armazena SEMPRE a forma canônica
    (com o 9º dígito). Todo caminho de escrita (upsert_contact, upsert_patient,
    link_patient_contact) passa por normalize_phone antes de gravar. Toda busca
    normaliza antes de consultar. Por isso, diferente do app/database.py legado
    (que buscava as duas variantes com/sem 9), aqui basta buscar a forma canônica —
    não há contatos gravados na forma de 12 dígitos.
    """
    digits = _strip_phone(phone)
    if len(digits) == 13 and digits.startswith("55"):
        return digits
    if len(digits) == 12 and digits.startswith("55"):
        return digits[:4] + "9" + digits[4:]
    return digits


async def get_contact_by_phone(phone: str) -> dict | None:
    """Retorna a linha de `contacts` para este número (forma canônica), ou None.

    Tenta a forma canônica (com 9) primeiro. Se não encontrar, tenta a variante
    sem o 9 — necessário para contatos legados gravados antes da normalização.
    """
    from app.database import _phone_variants
    client = await get_supabase()
    for variant in _phone_variants(phone):
        result = (
            await client.from_("contacts")
            .select("*")
            .eq("phone", variant)
            .execute()
        )
        rows = result.data or []
        if rows:
            return rows[0]
    return None


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
    if not data.get("name"):
        # Sem nome não é possível criar um paciente válido — ignora silenciosamente.
        return None

    # Evita duplicar paciente já cadastrado sob outro contato (ex: cônjuge agendando
    # pelo próprio número) — mesmo nome + mesma data de nascimento é considerado a
    # mesma pessoa. Se achar, atualiza o existente em vez de inserir um novo.
    birth_date = data.get("birth_date")
    if birth_date:
        existing = (
            await client.from_("patients")
            .select("id")
            .eq("name", data["name"])
            .eq("birth_date", birth_date)
            .execute()
        )
        if existing.data:
            existing_id = existing.data[0]["id"]
            await client.from_("patients").update(data).eq("id", existing_id).execute()
            return existing_id

    result = await client.from_("patients").insert(data).execute()
    inserted = (result.data or [{}])[0]
    return inserted.get("id")


async def link_patient_contact(
    patient_id: str, contact_id: str, role: str,
    is_self: bool | None = False, relationship: str | None = None,
) -> None:
    """Vincula um contato a um paciente com um papel. Idempotente.

    Usa a constraint UNIQUE(patient_id, contact_id, role).
    Quando is_self=None, garante que o link existe sem sobrescrever is_self/relationship.
    """
    client = await get_supabase()
    if is_self is None:
        # Only create the link if it doesn't exist — don't overwrite existing values
        await client.from_("patient_contacts").upsert(
            {
                "patient_id": patient_id,
                "contact_id": contact_id,
                "role": role,
                "is_self": False,
                "relationship": None,
            },
            on_conflict="patient_id,contact_id,role",
            ignore_duplicates=True,
        ).execute()
    else:
        await client.from_("patient_contacts").upsert(
            {
                "patient_id": patient_id,
                "contact_id": contact_id,
                "role": role,
                "is_self": is_self,
                "relationship": relationship,
            },
            on_conflict="patient_id,contact_id,role",
        ).execute()


async def _patient_has_upcoming_appointment(patient_id: str) -> bool:
    """True se o paciente tem agendamento futuro/ongoing (status scheduled)."""
    client = await get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()
    result = (
        await client.from_("appointments")
        .select("id")
        .eq("patient_id", patient_id)
        .in_("status", ["scheduled", "pending_reschedule"])
        .gte("end_time", now_iso)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def resolve_active_patient(phone: str) -> dict:
    """Resolve qual paciente está em contexto para um número.

    Retorna dict com chaves: contact, patient, candidates, ambiguous.
    Regras: 0 pacientes -> patient=None; 1 -> esse; 2+ -> se exatamente um tem
    agendamento próximo assume-o, senão ambiguous=True.
    """
    contact = await get_contact_by_phone(phone)
    if not contact:
        return {"contact": None, "patient": None, "candidates": [], "ambiguous": False}

    candidates = await get_patients_by_contact(contact["id"], role="agendamento")
    if not candidates:
        return {"contact": contact, "patient": None, "candidates": [], "ambiguous": False}
    if len(candidates) == 1:
        return {"contact": contact, "patient": candidates[0], "candidates": candidates, "ambiguous": False}

    upcoming = [c for c in candidates if await _patient_has_upcoming_appointment(c["id"])]
    if len(upcoming) == 1:
        return {"contact": contact, "patient": upcoming[0], "candidates": candidates, "ambiguous": False}
    return {"contact": contact, "patient": None, "candidates": candidates, "ambiguous": True}
