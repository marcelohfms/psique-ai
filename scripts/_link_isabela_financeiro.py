"""One-off: adiciona role 'financeiro' ao número ativo da própria Isabela
(self, 5581996689228). Antes, o único 'financeiro' era o número do pai
(possivelmente inativo) — risco latente de o lembrete de taxa falhar.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

ISABELA_PATIENT = "891ac742-116b-4370-a664-265cdb2c7d24"
ISABELA_CONTACT = "0e85991f-67d8-4e45-9ac0-038639c5c09f"  # self, 5581996689228


async def main():
    from app.database import get_supabase, link_patient_contact

    await link_patient_contact(
        ISABELA_PATIENT, ISABELA_CONTACT, "financeiro",
        is_self=True, relationship="self",
    )
    print("linked role=financeiro (self)")

    client = await get_supabase()
    pc = await client.from_("patient_contacts").select(
        "role, is_self, relationship, contacts(name, phone)"
    ).eq("patient_id", ISABELA_PATIENT).execute()
    print("\n=== patient_contacts de Isabela ===")
    for row in pc.data:
        c = row.get("contacts") or {}
        print(f"  role={row['role']:12} is_self={row['is_self']} rel={row.get('relationship')} | {c.get('name')} ({c.get('phone')})")


asyncio.run(main())
