"""Camada de dados nativa para patients / contacts / patient_contacts.

Substitui gradualmente o modelo antigo de `users` (ver app/database.py).
"""
import logging
import unicodedata
from datetime import date, datetime, timezone

from app.phone import _phone_variants, _strip_phone
from app.supabase_client import get_supabase

logger = logging.getLogger(__name__)


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


async def get_contact_by_id(contact_id: str | None) -> dict | None:
    """Retorna a linha de `contacts` por id, ou None (inclui id None)."""
    if not contact_id:
        return None
    client = await get_supabase()
    result = (
        await client.from_("contacts")
        .select("*")
        .eq("id", contact_id)
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


async def get_contacts_for_patient(patient_id: str, role: str, include_inactive: bool = False) -> list[dict]:
    """Retorna os contatos com o papel `role` para um paciente.

    Por padrão retorna só contatos ATIVOS. Passe `include_inactive=True` para
    lembretes/confirmações transacionais de consulta, que devem chegar mesmo
    com o contato pausado (ex.: transferido para atendimento humano) — pausa
    do bot não deve silenciar avisos de horário de consulta.
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
        if contact and (include_inactive or contact.get("active")) and contact["id"] not in seen:
            seen.add(contact["id"])
            out.append(contact)
    return out


async def get_reminder_contacts(
    patient_id: str, role: str, include_inactive: bool = False
) -> list[dict]:
    """Contatos que devem receber um lembrete de consulta/retorno.

    Regra: paciente ADULTO (idade >= 18) que tem ao menos um contato próprio
    (is_self=True) recebe o lembrete SÓ nesse(s) contato(s) — os responsáveis
    são omitidos. Menor de idade, paciente sem contato próprio, ou birth_date
    ausente/imparseável caem no comportamento padrão: todos os contatos do
    papel (mesma semântica active/include_inactive de get_contacts_for_patient).
    """
    client = await get_supabase()
    result = (
        await client.from_("patient_contacts")
        .select("contact_id, is_self, contacts(*)")
        .eq("patient_id", patient_id)
        .eq("role", role)
        .execute()
    )

    seen: set[str] = set()
    rows: list[dict] = []
    for row in (result.data or []):
        contact = row.get("contacts")
        if contact and (include_inactive or contact.get("active")) and contact["id"] not in seen:
            seen.add(contact["id"])
            rows.append({"is_self": bool(row.get("is_self")), "contact": contact})

    self_contacts = [r["contact"] for r in rows if r["is_self"]]
    if self_contacts:
        patient = await get_patient_by_id(patient_id)
        age = _compute_age((patient or {}).get("birth_date"))
        if age is not None and age >= 18:
            return self_contacts
    return [r["contact"] for r in rows]


def normalize_person_name(name: str | None) -> str:
    """Forma canônica de um nome para COMPARAÇÃO (nunca para gravação):
    sem acentos, minúsculas, espaços internos colapsados."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", name or "")
        if not unicodedata.combining(c)
    )
    return " ".join(stripped.lower().split())


def _birth_date_variants(birth_date: str) -> list[str]:
    """`patients.birth_date` convive com dd/mm/aaaa (fluxo do chat) e ISO
    (imports/scripts). Devolve as duas grafias da mesma data para a busca."""
    raw = (birth_date or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw, fmt)
            return [d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d")]
        except ValueError:
            continue
    return [raw]


def _compute_age(birth_date: str | None) -> int | None:
    """Idade em anos completos a partir de `patients.birth_date`.

    Aceita as duas grafias que convivem no banco (dd/mm/aaaa do chat e ISO de
    imports). Retorna None quando ausente ou não parseável — o chamador trata
    None como "idade desconhecida" e NÃO suprime contatos nesse caso.
    """
    raw = (birth_date or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            bd = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None
    today = date.today()
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))


async def get_patient_by_id(patient_id: str) -> dict | None:
    """Retorna a linha de `patients` por id, ou None."""
    client = await get_supabase()
    result = (
        await client.from_("patients")
        .select("*")
        .eq("id", patient_id)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


async def find_patient_by_name_birth(
    name: str, birth_date: str, exclude_id: str | None = None,
) -> dict | None:
    """Busca UM paciente por nome normalizado + data de nascimento.

    Data de nascimento igual é obrigatória (proteção contra homônimos); se mais
    de um paciente casar, é ambíguo e devolve None — melhor não vincular do que
    vincular ao homônimo errado.
    """
    target = normalize_person_name(name)
    if not target or not birth_date:
        return None
    client = await get_supabase()
    result = (
        await client.from_("patients")
        .select("*")
        .in_("birth_date", _birth_date_variants(birth_date))
        .execute()
    )
    hits = [
        row for row in (result.data or [])
        if row.get("id") != exclude_id
        and normalize_person_name(row.get("name")) == target
    ]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        logger.warning(
            "PACIENTE_HOMONIMO_AMBIGUO: %r + %s casa com %d pacientes (%s) — "
            "nenhum vínculo feito",
            name, birth_date, len(hits), [h.get("id") for h in hits],
        )
    return None


async def merge_duplicate_patient(dup_id: str | None, target_id: str | None) -> bool:
    """Mescla um paciente DUPLICADO no cadastro existente e devolve True.

    Migra as consultas do duplicado para o alvo (repoint de patient_id), reponta
    os vínculos de patient_contacts e apaga o duplicado. É self-healing: mesmo
    quando o duplicado JÁ tem consulta agendada, consolida tudo num único
    prontuário em vez de recusar (caso Catarina, 5581999273053, 19/08/2026: a
    ficha nova, criada no passo do nome, já tinha a consulta agendada quando a
    reconciliação por nome+nascimento tentou mesclar — o antigo guard recusava e
    a duplicata sobrevivia). A segurança contra homônimos fica em quem chama:
    find_patient_by_name_birth só devolve match ÚNICO por nome normalizado +
    data de nascimento exata.
    """
    if not dup_id or not target_id or dup_id == target_id:
        return False
    client = await get_supabase()
    # Migra qualquer consulta do duplicado para o cadastro real — nenhum
    # histórico é perdido ao apagar o duplicado logo abaixo.
    await client.from_("appointments").update(
        {"patient_id": target_id}
    ).eq("patient_id", dup_id).execute()
    links = (
        await client.from_("patient_contacts")
        .select("*")
        .eq("patient_id", dup_id)
        .execute()
    )
    for row in (links.data or []):
        await link_patient_contact(
            target_id, row["contact_id"], row["role"],
            is_self=row.get("is_self"), relationship=row.get("relationship"),
        )
    await client.from_("patient_contacts").delete().eq("patient_id", dup_id).execute()
    await client.from_("patients").delete().eq("id", dup_id).execute()
    logger.info("MERGE_PACIENTE: duplicado %s mesclado em %s", dup_id, target_id)
    return True


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
    # mesma pessoa. A comparação de nome é normalizada (acentos/caixa/espaços): a
    # grafia varia entre conversas e "Maria Jose" não pode virar um segundo
    # prontuário de "Maria José". Se achar match único, atualiza o existente.
    birth_date = data.get("birth_date")
    if birth_date:
        existing = await find_patient_by_name_birth(data["name"], birth_date)
        if existing:
            await client.from_("patients").update(data).eq("id", existing["id"]).execute()
            return existing["id"]

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
