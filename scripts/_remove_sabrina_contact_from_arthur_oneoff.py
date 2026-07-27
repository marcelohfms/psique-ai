"""One-off: remove o vínculo do contato da Sabrina (esposa) com o paciente
Arthur Tenório Ribeiro Clark, a pedido do usuário.

Paciente: d294bfd2-cd7d-468d-a065-18dfcdde9429
Contato removido: 97f577e0-5b82-4a2e-befe-c2aa0aa93a31 (Sabrina Gomes Ferreira
Clark, 5581995821211, papéis agendamento/consulta/financeiro).

Não apaga o registro do contato em si (contacts), só os 3 vínculos em
patient_contacts. O histórico de appointments não é afetado (appointments
referencia contact_id diretamente).
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PATIENT_ID = "d294bfd2-cd7d-468d-a065-18dfcdde9429"
CONTACT_ID = "97f577e0-5b82-4a2e-befe-c2aa0aa93a31"


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    before = (
        await client.from_("patient_contacts")
        .select("*")
        .eq("patient_id", PATIENT_ID)
        .eq("contact_id", CONTACT_ID)
        .execute()
    ).data
    print(f"Removendo {len(before)} vínculo(s):")
    for pc in before:
        print(" ", pc["role"], pc["id"])

    await (
        client.from_("patient_contacts")
        .delete()
        .eq("patient_id", PATIENT_ID)
        .eq("contact_id", CONTACT_ID)
        .execute()
    )

    remaining = (
        await client.from_("patient_contacts")
        .select("*, contacts(name, phone)")
        .eq("patient_id", PATIENT_ID)
        .execute()
    ).data
    print("\n=== Vínculos restantes do paciente ===")
    for pc in remaining:
        print(" ", pc["role"], pc["contacts"])


asyncio.run(main())
