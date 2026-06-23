"""Backfill idempotente: users -> patients + contacts + patient_contacts.

Idempotência:
- patients: usa patients.legacy_user_id == users.id (não recria)
- contacts: usa contacts.phone UNIQUE (não recria)
- patient_contacts: UNIQUE(patient_id, contact_id, role) via upsert

Roda uma vez após a migration da Phase 1. Seguro reexecutar.
NÃO altera nem apaga a tabela `users` (apenas lê).

Uso:
    uv run python scripts/migrate_users_to_patients_contacts.py [--dry-run]
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

from app.database import get_supabase
from app.patients import normalize_phone, link_patient_contact

# Campos de users que pertencem ao paciente (clínicos).
_PATIENT_FIELDS = [
    "email", "birth_date", "age", "doctor_id", "is_returning_patient",
    "patient_cpf",
    "consultation_reason", "referral_professional", "modality_restriction",
    "age_exception", "custom_price", "booking_fee_waived",
    "financial_name", "financial_cpf", "financial_email",
]
# Campos de users que pertencem ao contato.
_CONTACT_FIELDS = [
    "active", "manual_hold", "deactivated_at", "price_adjustment_notified_at",
]
ROLES = ["agendamento", "financeiro", "consulta"]


def _patient_name(user: dict) -> str:
    # quando is_patient=False, o paciente é patient_name; senão é o name do contato
    if user.get("is_patient") is False and user.get("patient_name"):
        return user["patient_name"]
    return user.get("patient_name") or user.get("name") or "(sem nome)"


async def _get_or_create_contact(
    client, phone: str, name: str | None, cpf: str | None = None
) -> str:
    canonical = normalize_phone(phone)
    existing = await client.from_("contacts").select("id").eq("phone", canonical).execute()
    if existing.data:
        return existing.data[0]["id"]
    inserted = await client.from_("contacts").insert(
        {"phone": canonical, "name": name, "cpf": cpf}
    ).execute()
    return inserted.data[0]["id"]


def _contact_name_and_cpf(user: dict) -> tuple[str | None, str | None]:
    """Resolve nome/CPF do CONTATO conforme ele seja responsável ou o paciente."""
    if user.get("is_patient") is False:
        # contato é o responsável (menores)
        name = user.get("guardian_name") or user.get("name")
        cpf = user.get("guardian_cpf")
    else:
        # contato é o próprio paciente
        name = user.get("name")
        cpf = user.get("patient_cpf")
    return name, cpf


def _contact_relationship(user: dict) -> str | None:
    if user.get("is_patient") is False:
        return user.get("guardian_relationship")
    return "self"


async def _get_or_create_patient(client, user: dict) -> str:
    existing = await client.from_("patients").select("id").eq("legacy_user_id", user["id"]).execute()
    if existing.data:
        return existing.data[0]["id"]
    payload = {"name": _patient_name(user), "legacy_user_id": user["id"]}
    # birth_date é TEXT (mesmo padrão 'dd/mm/aaaa' da users) — copiado sem conversão
    for f in _PATIENT_FIELDS:
        if user.get(f) is not None:
            payload[f] = user[f]
    inserted = await client.from_("patients").insert(payload).execute()
    return inserted.data[0]["id"]


async def _fetch_all(client, table: str, columns: str = "*") -> list[dict]:
    """Lê TODAS as linhas paginando — o PostgREST limita cada select a 1000 linhas.

    Sem isso, tabelas com mais de 1000 registros seriam truncadas silenciosamente.
    """
    rows: list[dict] = []
    batch = 1000
    start = 0
    while True:
        chunk = (
            await client.from_(table).select(columns)
            .range(start, start + batch - 1).execute()
        ).data or []
        rows.extend(chunk)
        if len(chunk) < batch:
            break
        start += batch
    return rows


async def main(dry_run: bool) -> None:
    client = await get_supabase()
    users = await _fetch_all(client, "users")
    print(f"{len(users)} users encontrados")

    ok = 0
    failures: list[tuple[str, str]] = []
    for user in users:
        phone = user.get("number")
        if not phone:
            print(f"  SKIP user {user['id']} sem number")
            continue
        name = _patient_name(user)
        is_self = bool(user.get("is_patient"))
        if dry_run:
            print(f"  DRY user {user['id']} -> contact({normalize_phone(phone)}) "
                  f"+ patient({name}) is_self={is_self}")
            continue

        try:
            contact_name, contact_cpf = _contact_name_and_cpf(user)
            relationship = _contact_relationship(user)
            contact_id = await _get_or_create_contact(client, phone, contact_name, contact_cpf)
            contact_update = {f: user[f] for f in _CONTACT_FIELDS if user.get(f) is not None}
            if contact_cpf is not None:
                contact_update["cpf"] = contact_cpf
            if contact_name is not None:
                contact_update["name"] = contact_name
            if contact_update:
                await client.from_("contacts").update(contact_update).eq("id", contact_id).execute()

            patient_id = await _get_or_create_patient(client, user)
            for role in ROLES:
                await link_patient_contact(
                    patient_id, contact_id, role,
                    is_self=is_self, relationship=relationship,
                )
            ok += 1
            print(f"  OK user {user['id']} -> patient {patient_id} / contact {contact_id}")
        except Exception as e:  # noqa: BLE001 — registra e continua, não trava o lote
            failures.append((user["id"], str(e)))
            print(f"  FAIL user {user['id']} ({name}): {e}")

    if not dry_run:
        print("Atualizando appointments.patient_id...")
        appts = await _fetch_all(client, "appointments", "id, user_id")
        for appt in appts:
            if not appt.get("user_id"):
                continue
            p = await client.from_("patients").select("id").eq("legacy_user_id", appt["user_id"]).execute()
            if p.data:
                await client.from_("appointments").update(
                    {"patient_id": p.data[0]["id"]}
                ).eq("id", appt["id"]).execute()
        print("appointments atualizados")

    if not dry_run:
        print(f"\n=== Resumo: {ok} OK, {len(failures)} falhas ===")
        for uid, err in failures:
            print(f"  FAIL {uid}: {err}")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
