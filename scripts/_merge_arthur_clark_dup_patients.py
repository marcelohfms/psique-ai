"""One-off: mescla os dois cadastros duplicados de Arthur Tenório Ribeiro Clark.

Canônico: d294bfd2 (criado 06/07, tem consultation_reason/referral_professional
preenchidos — histórico clínico sensível — e a consulta concluída de 10/07,
vinculado ao contato da esposa Sabrina).

Duplicado: aa7cf66e (criado 14/07, vinculado ao número do próprio Arthur,
tem a consulta de hoje 27/07 16h agendada, is_returning_patient=True).

Usa scripts/merge_duplicate_patients.merge() (repontar appointments, religar
patient_contacts, apagar duplicado) e depois corrige is_returning_patient no
canônico para True (o merge genérico só preenche campos vazios, e o canônico
já tinha False antes da consulta de 10/07 contar como "retorno").
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

CANONICAL = "d294bfd2-cd7d-468d-a065-18dfcdde9429"
DUPLICATE = "aa7cf66e-d53a-40eb-9dcf-65e393042e88"


async def main():
    import sys
    sys.path.insert(0, "scripts")
    from merge_duplicate_patients import merge
    from app.database import get_supabase

    print("=== DRY RUN ===")
    await merge(CANONICAL, [DUPLICATE], dry_run=True)

    print("\n=== EXEC ===")
    await merge(CANONICAL, [DUPLICATE], dry_run=False)

    client = await get_supabase()
    await client.from_("patients").update({"is_returning_patient": True}).eq("id", CANONICAL).execute()
    print("\n✅ is_returning_patient=True aplicado ao canônico")

    final = await client.from_("patients").select("*").eq("id", CANONICAL).execute()
    print("\n=== Paciente final ===")
    print(final.data)

    appts = await client.from_("appointments").select("*").eq("patient_id", CANONICAL).order("start_time").execute()
    print("\n=== Agendamentos finais ===")
    for a in appts.data:
        print(" ", a["start_time"], a["status"], a["modality"])

    pcs = await client.from_("patient_contacts").select("*").eq("patient_id", CANONICAL).execute()
    print("\n=== Contatos vinculados ===")
    for pc in pcs.data:
        print(" ", pc)

asyncio.run(main())
