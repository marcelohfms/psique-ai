"""Camada de dados do painel da atendente (Fase 1).

Autocontida: replica as poucas queries necessárias usando o cliente Supabase
do dashboard. NÃO importa app/ (a imagem Docker do dashboard não contém app/).
"""
import unicodedata
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from db_client import get_client

_TZ = ZoneInfo("America/Recife")


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
    """Pacientes ligados ao contato, sem repetição, cada um com `link`: o
    marcador do par (id da linha, is_self, relationship). Prefere a linha
    `agendamento`, a mesma que get_link devolve ao painel."""
    res = (
        await client.from_("patient_contacts")
        .select("id, patient_id, role, is_self, relationship, patients(*)")
        .eq("contact_id", contact_id)
        .execute()
    )
    by_id: dict[str, dict] = {}
    for row in (res.data or []):
        patient = row.get("patients")
        if not patient:
            continue
        marker = {
            "id": row.get("id"),
            "is_self": bool(row.get("is_self")),
            "relationship": row.get("relationship"),
        }
        entry = by_id.get(patient["id"])
        if entry is None:
            by_id[patient["id"]] = {**patient, "link": marker}
        elif row.get("role") == "agendamento":
            entry["link"] = marker
    return list(by_id.values())


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
    if not rows:
        return None
    for r in rows:
        if r.get("role") == "agendamento":
            return r
    return rows[0]


async def get_return_reminder(patient_id: str) -> dict | None:
    """Linha de return_reminders do paciente (1 por paciente) ou None.

    A data de retorno mora nesta tabela separada, não em `patients`.

    SELECT escopado (diferente dos `get_*` irmãos, que usam `*`): não vaza ao
    painel campos internos como `last_classified_appointment_id` e as flags de
    envio (`month_before_sent_at`, `month_of_sent_at`, `overdue_sent_at`).
    """
    client = await get_client()
    res = (
        await client.from_("return_reminders")
        .select("next_return_date, return_interval, doctor_id")
        .eq("patient_id", patient_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


# ── Busca de paciente (vincular) ──────────────────────────────────────────────

SEARCH_MIN_CHARS = 3
_ACCENTABLE = set("aeiouc")


def _norm(text: str | None) -> str:
    """Sem acento, minúsculo, espaços colapsados. Espelha normalize_person_name."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)
    )
    return " ".join(stripped.lower().split())


def _ilike_pattern(query: str) -> str:
    """Padrão ILIKE que tolera acento: toda letra que pode ter acento vira `_`
    (um caractere qualquer). Espaço vira `%` (tolera espaço duplo gravado no
    banco). O filtro exato sem acento é feito depois, em Python."""
    folded = _norm(query)
    mapped = "".join("_" if ch in _ACCENTABLE else ch for ch in folded)
    return "%" + mapped.replace(" ", "%") + "%"


_SEARCH_PAGE_SIZE = 1000


async def search_patients(query: str, limit: int = 10) -> list[dict]:
    """Pacientes cujo nome contém `query` (sem acento e sem caixa).

    Devolve só o necessário para diferenciar homônimos: id, nome, nascimento e
    os 4 últimos dígitos do número próprio do paciente (phone_hint), se houver.

    O ILIKE é só uma pré-filtragem grosseira (tolera acento trocando vogais por
    `_`), então pode casar muito mais nomes do que o esperado (ex: "ana" vira
    `%_n_%`). Por isso pagina TODOS os candidatos (sem cap arbitrário) antes de
    aplicar o filtro exato em Python — um `.limit()` direto no ILIKE poderia
    cortar a página antes de ela conter o paciente certo.
    """
    target = _norm(query)
    if len(target) < SEARCH_MIN_CHARS:
        return []
    client = await get_client()
    pattern = _ilike_pattern(query)
    candidates: list[dict] = []
    start = 0
    while True:
        res = await (
            client.from_("patients")
            .select("id, name, birth_date")
            .ilike("name", pattern)
            .order("id")
            .range(start, start + _SEARCH_PAGE_SIZE - 1)
            .execute()
        )
        page = res.data or []
        if not page:
            break
        candidates.extend(page)
        start += len(page)

    hits = [r for r in candidates if target in _norm(r.get("name"))]
    hits.sort(key=lambda r: _norm(r.get("name")))
    hits = hits[:limit]
    if not hits:
        return []

    pcs = await (
        client.from_("patient_contacts")
        .select("patient_id, is_self, contacts(phone)")
        .in_("patient_id", [h["id"] for h in hits])
        .eq("is_self", True)
        .execute()
    )
    hint_by_patient: dict[str, str] = {}
    for row in (pcs.data or []):
        phone = (row.get("contacts") or {}).get("phone") or ""
        if phone and row["patient_id"] not in hint_by_patient:
            hint_by_patient[row["patient_id"]] = phone[-4:]

    return [
        {"id": h["id"], "name": h.get("name"), "birth_date": h.get("birth_date"),
         "phone_hint": hint_by_patient.get(h["id"])}
        for h in hits
    ]


# ── Vincular e desvincular ────────────────────────────────────────────────────

LINK_ROLES = ("agendamento", "financeiro", "consulta")


async def link_patient(patient_id: str, contact_id: str, marker: dict) -> None:
    """Liga o contato ao paciente nos três papéis, com o mesmo marcador.

    Um único upsert pela UNIQUE(patient_id, contact_id, role): idempotente e
    atômico — chamar de novo mantém as mesmas 3 linhas e a marcação da última
    chamada vence em todas. A regra da idade decide na hora do envio quem
    recebe o quê; aqui o que importa é o marcador estar certo e igual nas três.
    """
    client = await get_client()
    rows = [
        {"patient_id": patient_id, "contact_id": contact_id, "role": role, **marker}
        for role in LINK_ROLES
    ]
    await (
        client.from_("patient_contacts")
        .upsert(rows, on_conflict="patient_id,contact_id,role")
        .execute()
    )


def _age_in_years(born, today) -> int:
    """Idade em anos completos, considerando se o aniversário do ano já passou."""
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


_MAX_AGE_YEARS = 120


def normalize_birth_date(raw: str) -> str:
    """Aceita dd/mm/aaaa ou aaaa-mm-dd e devolve dd/mm/aaaa, o formato que o
    fluxo do chat grava em patients.birth_date. ValueError se inválida, futura
    ou implausível (mais de 120 anos). "Hoje" é sempre o dia em
    America/Recife, não o fuso do servidor."""
    text = (raw or "").strip()
    today = datetime.now(_TZ).date()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if d > today:
            raise ValueError("Data de nascimento no futuro.")
        if _age_in_years(d, today) > _MAX_AGE_YEARS:
            raise ValueError("Data de nascimento implausível (mais de 120 anos).")
        return d.strftime("%d/%m/%Y")
    raise ValueError("Data de nascimento inválida. Use dd/mm/aaaa.")


def _birth_variants(birth_br: str) -> list[str]:
    d = datetime.strptime(birth_br, "%d/%m/%Y")
    return [d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d")]


async def find_patients_by_name_birth(name: str, birth_br: str) -> list[dict]:
    """Fichas com o mesmo nome (sem acento/caixa/espaços) e o mesmo nascimento,
    nas duas grafias que convivem no banco. Espelha find_patient_by_name_birth."""
    client = await get_client()
    res = await (
        client.from_("patients")
        .select("id, name, birth_date")
        .in_("birth_date", _birth_variants(birth_br))
        .execute()
    )
    target = _norm(name)
    return [r for r in (res.data or []) if _norm(r.get("name")) == target]


async def create_patient(name: str, birth_br: str) -> dict:
    """Cria a ficha com nome e nascimento. O id é gerado aqui para a resposta
    não depender do retorno do insert. Grava também `age` (anos completos): a
    Eva lê `patients.age` para a regra do paciente menor de idade."""
    clean_name = " ".join(name.split())
    if not clean_name:
        raise ValueError("Nome vazio.")
    born = datetime.strptime(birth_br, "%d/%m/%Y").date()
    today = datetime.now(_TZ).date()
    row = {
        "id": str(uuid.uuid4()),
        "name": clean_name,
        "birth_date": birth_br,
        "age": _age_in_years(born, today),
    }
    client = await get_client()
    await client.from_("patients").insert(row).execute()
    return row


_ACTIVE_APPT_STATUSES = ("scheduled", "pending_reschedule")


async def unlink_blocker(patient_id: str, contact_id: str) -> str | None:
    """Motivo para NÃO desvincular, ou None se pode.

    Trava só o caso perigoso: é o único número do paciente e ele tem consulta
    futura ativa (ficaria sem ninguém para receber lembrete e cobrança).
    """
    client = await get_client()
    others = await (
        client.from_("patient_contacts")
        .select("contact_id")
        .eq("patient_id", patient_id)
        .neq("contact_id", contact_id)
        .in_("role", ["agendamento", "consulta"])
        .execute()
    )
    if others.data:
        return None
    appts = await (
        client.from_("appointments")
        .select("start_time")
        .eq("patient_id", patient_id)
        .in_("status", list(_ACTIVE_APPT_STATUSES))
        .gt("start_time", datetime.now(_TZ).isoformat())
        .order("start_time")
        .limit(1)
        .execute()
    )
    if not appts.data:
        return None
    when = datetime.fromisoformat(appts.data[0]["start_time"]).astimezone(_TZ)
    return (
        f"Este é o único número do paciente e ele tem consulta em "
        f"{when.strftime('%d/%m/%Y às %H:%M')}. Vincule outro número antes."
    )


async def unlink_patient(patient_id: str, contact_id: str) -> int:
    """Apaga todas as linhas do par (os três papéis). Retorna quantas saíram."""
    client = await get_client()
    res = await (
        client.from_("patient_contacts").delete()
        .eq("patient_id", patient_id)
        .eq("contact_id", contact_id)
        .execute()
    )
    return len(res.data or [])


# ── Escopo por telefone (anti-IDOR) ───────────────────────────────────────────


async def scope_for_phone(phone: str) -> tuple[str | None, set[str]]:
    """Resolve o telefone da requisição para (contact_id, {patient_ids}).

    É o conjunto de objetos que o painel pode legitimamente tocar naquela
    conversa. As rotas de escrita comparam o ID alvo contra isto antes de mexer
    no banco, para o token do painel não virar chave-mestra sobre qualquer ficha.
    """
    resolved = await resolve_contact_and_patients(phone)
    contact = resolved.get("contact")
    contact_id = contact["id"] if contact else None
    patient_ids = {p["id"] for p in resolved.get("patients", [])}
    return contact_id, patient_ids


async def get_link_by_id(pc_id: str) -> dict | None:
    """Linha de patient_contacts pelo id (id, patient_id, contact_id) ou None."""
    client = await get_client()
    res = (
        await client.from_("patient_contacts")
        .select("id, patient_id, contact_id")
        .eq("id", pc_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


async def get_appointment_patient_id(appointment_id: str) -> str | None:
    """patient_id da consulta, ou None se a consulta não existe/está sem vínculo.

    Filtra por `appointment_id` (id do evento do Google Calendar, texto), a mesma
    coluna que payments.mark_paid usa. NÃO usar `id` (UUID interno): passar o
    appointment_id ali estoura com erro de UUID no Postgres.
    """
    client = await get_client()
    res = (
        await client.from_("appointments")
        .select("patient_id")
        .eq("appointment_id", appointment_id)
        .execute()
    )
    rows = res.data or []
    return rows[0]["patient_id"] if rows else None


# ── Updates com whitelist de campos ───────────────────────────────────────────

_CONTACT_FIELDS = {"name", "cpf", "phone", "active", "manual_hold"}
_PATIENT_FIELDS = {
    "name", "birth_date", "age", "patient_cpf", "email", "doctor_id",
    "is_returning_patient", "modality_restriction", "age_exception", "custom_price",
    "financial_name", "financial_cpf", "financial_email", "social_name",
    "booking_fee_waived",
}
_LINK_FIELDS = {"role", "is_self", "relationship"}
_RETURN_FIELDS = {"next_return_date"}


def _filter(data: dict, allowed: set[str]) -> dict:
    return {k: v for k, v in data.items() if k in allowed}


# ── Marcador do vínculo (próprio paciente x parentesco) ──────────────────────

# Lista fechada: é o que a regra da idade (app/patients.py) sabe interpretar.
# "tutor(a)" e "responsável legal" contam como responsável legal; "cônjuge" e
# "acompanhante" contam como terceiros que não são responsáveis.
RELATIONSHIPS = (
    "mãe", "pai", "avó", "avô", "tutor(a)", "responsável legal", "tio", "tia",
    "irmão", "irmã", "padrasto", "madrasta", "cônjuge", "acompanhante",
)


def normalize_marker(is_self, relationship) -> dict:
    """Valida e normaliza o marcador de um par (paciente, contato).

    A regra da idade só trata o número como "próprio" quando is_self é True E o
    parentesco está vazio. Por isso "próprio" sempre grava relationship=None, e
    terceiro exige um parentesco da lista fechada. ValueError quando inválido.
    """
    if is_self is True:
        return {"is_self": True, "relationship": None}
    rel = (relationship or "").strip()
    if rel not in RELATIONSHIPS:
        raise ValueError("Escolha o parentesco da lista.")
    return {"is_self": False, "relationship": rel}


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


_MARKER_FIELDS = {"is_self", "relationship"}


async def update_link(pc_id: str, data: dict) -> None:
    """Atualiza um vínculo. `role` afeta só a linha `pc_id`; is_self/relationship
    são propriedade do PAR (paciente, contato) e são gravados em TODAS as roles
    do par — senão as linhas divergem e o cron de lembrete lê um valor e o painel
    mostra outro.
    """
    payload = _filter(data, _LINK_FIELDS)
    if not payload:
        return
    client = await get_client()
    marker = {k: v for k, v in payload.items() if k in _MARKER_FIELDS}
    role_only = {k: v for k, v in payload.items() if k not in _MARKER_FIELDS}

    if marker:
        link = await get_link_by_id(pc_id)
        if link:
            await (
                client.from_("patient_contacts").update(marker)
                .eq("patient_id", link["patient_id"])
                .eq("contact_id", link["contact_id"])
                .execute()
            )
    if role_only:
        await client.from_("patient_contacts").update(role_only).eq("id", pc_id).execute()


async def set_patient_eva_off(patient_id: str, off: bool) -> int:
    """Liga/desliga a Eva em definitivo para o paciente INTEIRO.

    Grava manual_hold=off em TODOS os contatos vinculados ao paciente. Retorna
    quantos contatos foram afetados. Reversível: off=False religa a Eva."""
    client = await get_client()
    res = await (
        client.from_("patient_contacts")
        .select("contact_id")
        .eq("patient_id", patient_id)
        .execute()
    )
    contact_ids = sorted({r["contact_id"] for r in (res.data or []) if r.get("contact_id")})
    for cid in contact_ids:
        await client.from_("contacts").update({"manual_hold": off}).eq("id", cid).execute()
    return len(contact_ids)


async def is_patient_eva_off(patient_id: str) -> bool:
    """True se algum contato vinculado ao paciente está em manual_hold
    (Eva desligada em definitivo)."""
    client = await get_client()
    res = await (
        client.from_("patient_contacts")
        .select("contacts(manual_hold)")
        .eq("patient_id", patient_id)
        .execute()
    )
    for row in (res.data or []):
        c = row.get("contacts") or {}
        if c.get("manual_hold"):
            return True
    return False


async def update_return_reminder(patient_id: str, data: dict) -> bool:
    """Atualiza a data de retorno do paciente e zera as flags de envio.

    Só faz UPDATE (não cria linha): se o paciente ainda não foi classificado
    pela médica, nada acontece e retorna False. Zerar as flags realinha o cron
    `scripts/send_return_reminders.py` para disparar os lembretes na nova data.

    Retorna `bool` (diferente de `update_contact`/`update_patient`/`update_link`,
    que retornam None): o chamador precisa distinguir "não existe linha ainda"
    de "atualizado".
    """
    payload = _filter(data, _RETURN_FIELDS)
    if not payload:
        return False
    payload["month_before_sent_at"] = None
    payload["month_of_sent_at"] = None
    payload["overdue_sent_at"] = None
    payload["updated_at"] = datetime.now(_TZ).isoformat()
    client = await get_client()
    res = (
        await client.from_("return_reminders")
        .update(payload)
        .eq("patient_id", patient_id)
        .execute()
    )
    return bool(res.data)


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
