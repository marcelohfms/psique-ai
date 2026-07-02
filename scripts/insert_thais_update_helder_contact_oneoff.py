"""
One-off:
1. Insere Thais Irineu Moura Alencar Falcao como paciente própria no contato 5581988880299 (Dra. Bruna).
2. Atualiza o campo 'name' no registro de contato de Helder (id=c062e10b) para o nome completo da Thais.
Uso: uv run python scripts/insert_thais_update_helder_contact_oneoff.py
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

NUMBER = "5581988880299"
DOCTOR_ID_BRUNA = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
HELDER_CONTACT_ID = "c062e10b-1296-4e2a-bfd0-9a9d3eb3358c"

THAIS_PATIENT = {
    "number": NUMBER,
    "name": "Thais Irineu Moura Alencar Falcao",
    "patient_name": "Thais Irineu Moura Alencar Falcao",
    "birth_date": "25/06/1992",
    "age": 33,
    "patient_cpf": "053.046.324-51",
    "email": "thaisirineu_@hotmail.com",
    "doctor_id": DOCTOR_ID_BRUNA,
    "is_patient": True,
    "active": True,
}


async def main():
    from app.database import get_supabase, get_users_by_phone

    client = await get_supabase()

    # --- 1. Inserir Thais como paciente própria ---
    existing = await get_users_by_phone(NUMBER)
    print(f"Registros existentes para {NUMBER}:")
    for u in existing:
        print(f"  - {u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')} (id={u['id']})")

    thais_row = next(
        (r for r in existing if r.get("is_patient") is True and
         "thais" in (r.get("patient_name") or r.get("name") or "").lower()),
        None,
    )
    if thais_row:
        await client.from_("users").update(THAIS_PATIENT).eq("id", thais_row["id"]).execute()
        print(f"\nAtualizado registro próprio de Thais (id={thais_row['id']})")
    else:
        result = await client.from_("users").insert(THAIS_PATIENT).execute()
        inserted = (result.data or [{}])[0]
        print(f"\nInserido: Thais Irineu Moura Alencar Falcao (id={inserted.get('id')})")

    # --- 2. Atualizar nome no contato do Helder ---
    await client.from_("users").update({
        "name": "Thais Irineu Moura Alencar Falcao",
    }).eq("id", HELDER_CONTACT_ID).execute()
    print(f"Atualizado name no contato de Helder (id={HELDER_CONTACT_ID}) → 'Thais Irineu Moura Alencar Falcao'")

    # --- Estado final ---
    updated = await get_users_by_phone(NUMBER)
    print(f"\nRegistros finais para {NUMBER}:")
    for u in updated:
        print(f"  - name='{u.get('name')}' | patient_name='{u.get('patient_name')}' | is_patient={u.get('is_patient')} (id={u['id']})")


asyncio.run(main())
