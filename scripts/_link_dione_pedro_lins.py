"""One-off: corrige o contato da Dione (assessora da mãe de Pedro Lins), que
foi vítima do bug de paciente-fantasma (mensagem longa virou nome do contato
e criou um paciente-fantasma self-linkado). Renomeia o contato, remove o
fantasma e vincula corretamente ao paciente real Pedro Lins De Araújo.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PEDRO_ARAUJO = "150d4395-a058-466f-95cb-54bb46b5c312"   # patient_id real
GHOST_PATIENT = "67b1673f-657d-4f24-8f55-f0d6bf22eba2"  # patient_id fantasma
DIONE_CONTACT = "22c316cf-c8f2-4d98-a70b-42ad32b1d6a2"  # contact_id


async def main():
    from app.database import get_supabase, link_patient_contact

    client = await get_supabase()

    await client.from_("contacts").update({"name": "Dione"}).eq("id", DIONE_CONTACT).execute()
    print("contato renomeado para Dione")

    await client.from_("patient_contacts").delete().eq("patient_id", GHOST_PATIENT).execute()
    print("links do paciente-fantasma removidos")

    await client.from_("patients").delete().eq("id", GHOST_PATIENT).execute()
    print("paciente-fantasma apagado")

    for role in ("agendamento", "financeiro", "consulta"):
        await link_patient_contact(
            PEDRO_ARAUJO, DIONE_CONTACT, role,
            is_self=False, relationship="assessora da mãe",
        )
        print(f"linked role={role}")

    print("\n=== patient_contacts de Pedro Lins De Araújo ===")
    pc = await client.from_("patient_contacts").select(
        "role, is_self, relationship, contacts(name, phone)"
    ).eq("patient_id", PEDRO_ARAUJO).execute()
    for row in pc.data:
        c = row.get("contacts") or {}
        print(f"  role={row['role']:12} is_self={row['is_self']} rel={row.get('relationship')} | {c.get('name')} ({c.get('phone')})")

    ghost_check = await client.from_("patients").select("id").eq("id", GHOST_PATIENT).execute()
    print(f"\nfantasma ainda existe? {bool(ghost_check.data)}")


asyncio.run(main())
