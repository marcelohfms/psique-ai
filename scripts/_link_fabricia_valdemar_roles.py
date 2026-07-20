"""One-off: adiciona a Fabrícia (contato) aos roles financeiro e consulta do
paciente Valdemar. O role 'agendamento' já existia. Sem isso, a cobrança
automática (send_payment_reminders) mira o número inativo do próprio Valdemar.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

VALDEMAR = "4095a643-dfc8-47bc-a348-86b8b4d2a242"   # patient_id
FABRICIA = "a108f0d2-b582-4a3c-9eca-2ab0c897e5a2"   # contact_id


async def main():
    from app.database import get_supabase, link_patient_contact

    for role in ("financeiro", "consulta"):
        await link_patient_contact(
            VALDEMAR, FABRICIA, role,
            is_self=False, relationship="mãe",
        )
        print(f"linked role={role}")

    # verifica resultado
    client = await get_supabase()
    pc = await client.from_("patient_contacts").select(
        "role, is_self, relationship, contacts(name, phone)"
    ).eq("patient_id", VALDEMAR).execute()
    print("\n=== patient_contacts de Valdemar ===")
    for row in pc.data:
        c = row.get("contacts") or {}
        print(f"  role={row['role']:12} is_self={row['is_self']} rel={row.get('relationship')} | {c.get('name')} ({c.get('phone')})")


asyncio.run(main())
