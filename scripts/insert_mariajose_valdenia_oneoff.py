"""
One-off:
1. Corrige is_patient=False no registro Valdenia → Maria Eduarda.
2. Insere Maria José Alves de Farias (sogra de Valdenia) como nova paciente (Dra. Bruna).
Uso: uv run python scripts/insert_mariajose_valdenia_oneoff.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

NUMBER = "5581981139373"
DOCTOR_ID_BRUNA = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
MARIA_EDUARDA_ID = "e22d6f32-725d-49bd-b309-ed44130e050e"

MARIA_JOSE = {
    "number": NUMBER,
    "name": "Valdenia",
    "patient_name": "Maria José Alves de Farias",
    "guardian_name": "Valdenia",
    "guardian_relationship": "nora",
    "doctor_id": DOCTOR_ID_BRUNA,
    "is_patient": False,
    "active": True,
}


async def main():
    from app.database import get_supabase, get_users_by_phone

    client = await get_supabase()

    # 1. Corrige is_patient do registro existente (Valdenia → Maria Eduarda)
    await client.from_("users").update({"is_patient": False}).eq("id", MARIA_EDUARDA_ID).execute()
    print(f"✅ Corrigido is_patient=False em Valdenia → Maria Eduarda (id={MARIA_EDUARDA_ID})")

    # 2. Insere Maria José
    existing = await get_users_by_phone(f"{NUMBER}@s.whatsapp.net")
    mj_row = next(
        (r for r in existing if "maria josé" in (r.get("patient_name") or "").lower()),
        None,
    )
    if mj_row:
        await client.from_("users").update(MARIA_JOSE).eq("id", mj_row["id"]).execute()
        print(f"✅ Atualizado: Maria José Alves de Farias (id={mj_row['id']})")
    else:
        result = await client.from_("users").insert(MARIA_JOSE).execute()
        inserted = (result.data or [{}])[0]
        print(f"✅ Inserido: Maria José Alves de Farias (id={inserted.get('id')})")

    # Estado final
    updated = await get_users_by_phone(f"{NUMBER}@s.whatsapp.net")
    print(f"\nRegistros finais para {NUMBER}:")
    for u in updated:
        print(f"  name='{u.get('name')}' | patient_name='{u.get('patient_name')}' | is_patient={u.get('is_patient')} | doctor_id={u.get('doctor_id')}")


asyncio.run(main())
