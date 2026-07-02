"""Mescla pacientes duplicados (mesma pessoa cadastrada mais de uma vez).

Uso programático (via asyncio.run): merge(canonical_id, dup_ids, dry_run=True)

Ordem segura:
  1. Preenche campos faltantes do canônico a partir dos duplicados (não sobrescreve).
  2. Repona appointments dos duplicados para o canônico.
  3. Religa os contatos (patient_contacts) dos duplicados ao canônico (idempotente).
  4. Apaga os pacientes duplicados (CASCADE remove os vínculos órfãos).

NÃO apaga nada antes de repontar appointments (evita ON DELETE SET NULL).
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — database antes de patients (evita import circular)
from app.database import get_supabase
from app.patients import link_patient_contact

# Campos clínicos preenchíveis no canônico se estiverem vazios.
_FILLABLE = [
    "email", "birth_date", "age", "doctor_id", "is_returning_patient",
    "patient_cpf", "consultation_reason", "referral_professional",
    "modality_restriction", "age_exception", "custom_price",
    "booking_fee_waived", "financial_name", "financial_cpf", "financial_email",
]


async def merge(canonical_id: str, dup_ids: list[str], dry_run: bool = True) -> None:
    client = await get_supabase()

    canon = (await client.from_("patients").select("*").eq("id", canonical_id).single().execute()).data
    dups = [(await client.from_("patients").select("*").eq("id", d).single().execute()).data for d in dup_ids]

    # 1. Preencher campos faltantes do canônico
    fill = {}
    for f in _FILLABLE:
        if canon.get(f) in (None, ""):
            for d in dups:
                if d.get(f) not in (None, ""):
                    fill[f] = d[f]
                    break
    if fill:
        print(f"  [fill canônico] {fill}")
        if not dry_run:
            await client.from_("patients").update(fill).eq("id", canonical_id).execute()

    for d in dup_ids:
        # 2. Repontar appointments
        appts = (await client.from_("appointments").select("appointment_id").eq("patient_id", d).execute()).data or []
        print(f"  [appointments] {d[-6:]} -> {canonical_id[-6:]}: {len(appts)} agendamento(s)")
        if appts and not dry_run:
            await client.from_("appointments").update({"patient_id": canonical_id}).eq("patient_id", d).execute()

        # 3. Religar contatos
        links = (await client.from_("patient_contacts").select("contact_id, role, is_self, relationship").eq("patient_id", d).execute()).data or []
        print(f"  [contatos] {d[-6:]} -> {canonical_id[-6:]}: {len(links)} vínculo(s)")
        if not dry_run:
            for ln in links:
                await link_patient_contact(
                    canonical_id, ln["contact_id"], ln["role"],
                    is_self=ln.get("is_self", False), relationship=ln.get("relationship"),
                )

        # 4. Apagar duplicado
        print(f"  [delete] paciente {d[-6:]}")
        if not dry_run:
            await client.from_("patients").delete().eq("id", d).execute()

    print(f"  {'DRY-RUN (nada gravado)' if dry_run else 'MERGE CONCLUÍDO'} — canônico {canonical_id[-6:]}")


if __name__ == "__main__":
    # uso: python merge_duplicate_patients.py <canonical> <dup1> [dup2...] [--exec]
    args = [a for a in sys.argv[1:] if a != "--exec"]
    dry = "--exec" not in sys.argv
    asyncio.run(merge(args[0], args[1:], dry_run=dry))
