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

from app.database import get_supabase
from app.patients import normalize_phone, link_patient_contact

# Campos de users que pertencem ao paciente (clínicos).
_PATIENT_FIELDS = [
    "email", "birth_date", "age", "doctor_id", "is_returning_patient",
    "patient_cpf", "guardian_name", "guardian_cpf", "guardian_relationship",
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


async def _get_or_create_contact(client, phone: str, name: str | None) -> str:
    canonical = normalize_phone(phone)
    existing = await client.from_("contacts").select("id").eq("phone", canonical).execute()
    if existing.data:
        return existing.data[0]["id"]
    inserted = await client.from_("contacts").insert({"phone": canonical, "name": name}).execute()
    return inserted.data[0]["id"]


async def _get_or_create_patient(client, user: dict) -> str:
    existing = await client.from_("patients").select("id").eq("legacy_user_id", user["id"]).execute()
    if existing.data:
        return existing.data[0]["id"]
    payload = {"name": _patient_name(user), "legacy_user_id": user["id"]}
    for f in _PATIENT_FIELDS:
        if user.get(f) is not None:
            payload[f] = user[f]
    inserted = await client.from_("patients").insert(payload).execute()
    return inserted.data[0]["id"]


async def main(dry_run: bool) -> None:
    client = await get_supabase()
    users = (await client.from_("users").select("*").execute()).data or []
    print(f"{len(users)} users encontrados")

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

        contact_id = await _get_or_create_contact(client, phone, user.get("name"))
        contact_update = {f: user[f] for f in _CONTACT_FIELDS if user.get(f) is not None}
        if contact_update:
            await client.from_("contacts").update(contact_update).eq("id", contact_id).execute()

        patient_id = await _get_or_create_patient(client, user)
        for role in ROLES:
            await link_patient_contact(patient_id, contact_id, role, is_self=is_self)
        print(f"  OK user {user['id']} -> patient {patient_id} / contact {contact_id}")

    if not dry_run:
        print("Atualizando appointments.patient_id...")
        appts = (await client.from_("appointments").select("id, user_id").execute()).data or []
        for appt in appts:
            if not appt.get("user_id"):
                continue
            p = await client.from_("patients").select("id").eq("legacy_user_id", appt["user_id"]).execute()
            if p.data:
                await client.from_("appointments").update(
                    {"patient_id": p.data[0]["id"]}
                ).eq("id", appt["id"]).execute()
        print("appointments atualizados")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
