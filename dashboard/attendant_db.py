"""Camada de dados do painel da atendente (Fase 1).

Autocontida: replica as poucas queries necessárias usando o cliente Supabase
do dashboard. NÃO importa app/ (a imagem Docker do dashboard não contém app/).
"""
from db_client import get_client


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "").lstrip("+")


def _phone_variants(phone: str) -> list[str]:
    """Variantes com e sem o 9 de um celular brasileiro. Espelha app/database.py."""
    digits = _strip_phone(phone)
    if len(digits) == 13 and digits.startswith("55"):
        return [digits, digits[:4] + digits[5:]]
    if len(digits) == 12 and digits.startswith("55"):
        return [digits[:4] + "9" + digits[4:], digits]
    return [digits]


# ── Resolução de contato + pacientes ──────────────────────────────────────────


async def _get_contact_by_phone(client, phone: str) -> dict | None:
    for variant in _phone_variants(phone):
        res = await client.from_("contacts").select("*").eq("phone", variant).execute()
        rows = res.data or []
        if rows:
            return rows[0]
    return None


async def _get_patients_by_contact(client, contact_id: str) -> list[dict]:
    res = (
        await client.from_("patient_contacts")
        .select("patient_id, role, is_self, patients(*)")
        .eq("contact_id", contact_id)
        .execute()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for row in (res.data or []):
        patient = row.get("patients")
        if patient and patient["id"] not in seen:
            seen.add(patient["id"])
            out.append(patient)
    return out


async def resolve_contact_and_patients(phone: str) -> dict:
    """Retorna {"contact": <row|None>, "patients": [<row>, ...]} para um telefone."""
    client = await get_client()
    contact = await _get_contact_by_phone(client, phone)
    if not contact:
        return {"contact": None, "patients": []}
    patients = await _get_patients_by_contact(client, contact["id"])
    return {"contact": contact, "patients": patients}


# ── Leitura de paciente + vínculo ─────────────────────────────────────────────


async def get_patient(patient_id: str) -> dict | None:
    client = await get_client()
    res = await client.from_("patients").select("*").eq("id", patient_id).execute()
    rows = res.data or []
    return rows[0] if rows else None


async def get_link(patient_id: str, contact_id: str) -> dict | None:
    client = await get_client()
    res = (
        await client.from_("patient_contacts")
        .select("*")
        .eq("patient_id", patient_id)
        .eq("contact_id", contact_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


# ── Updates com whitelist de campos ───────────────────────────────────────────

_CONTACT_FIELDS = {"name", "cpf", "phone", "active", "manual_hold"}
_PATIENT_FIELDS = {
    "name", "birth_date", "age", "patient_cpf", "email", "doctor_id",
    "is_returning_patient", "modality_restriction", "age_exception", "custom_price",
    "financial_name", "financial_cpf", "financial_email",
}
_LINK_FIELDS = {"role", "is_self", "relationship"}


def _filter(data: dict, allowed: set[str]) -> dict:
    return {k: v for k, v in data.items() if k in allowed}


async def update_contact(contact_id: str, data: dict) -> None:
    payload = _filter(data, _CONTACT_FIELDS)
    if not payload:
        return
    client = await get_client()
    await client.from_("contacts").update(payload).eq("id", contact_id).execute()


async def update_patient(patient_id: str, data: dict) -> None:
    payload = _filter(data, _PATIENT_FIELDS)
    if not payload:
        return
    client = await get_client()
    await client.from_("patients").update(payload).eq("id", patient_id).execute()


async def update_link(pc_id: str, data: dict) -> None:
    payload = _filter(data, _LINK_FIELDS)
    if not payload:
        return
    client = await get_client()
    await client.from_("patient_contacts").update(payload).eq("id", pc_id).execute()


# ── Auditoria ─────────────────────────────────────────────────────────────────


async def log_event(event_type: str, phone: str, metadata: dict | None = None) -> None:
    try:
        client = await get_client()
        await client.from_("events").insert({
            "event_type": event_type,
            "phone": _strip_phone(phone),
            "metadata": metadata or {},
        }).execute()
    except Exception:
        pass  # auditoria nunca quebra o fluxo principal


# ── Reset do checkpoint ───────────────────────────────────────────────────────

_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")


async def reset_checkpoint(phone: str) -> int:
    """Apaga as linhas de checkpoint da Eva para um telefone (todas as variantes).

    Retorna o total de linhas removidas nas 3 tabelas. Cada DELETE é isolado em
    try/except — uma tabela ausente não impede as outras.
    """
    client = await get_client()
    thread_ids = [v + "@s.whatsapp.net" for v in _phone_variants(phone)]
    total = 0
    for table in _CHECKPOINT_TABLES:
        for tid in thread_ids:
            try:
                res = await client.from_(table).delete().eq("thread_id", tid).execute()
                total += len(res.data or [])
            except Exception:
                pass
    return total
