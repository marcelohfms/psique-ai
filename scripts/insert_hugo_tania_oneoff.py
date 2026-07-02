"""
One-off: cadastra Hugo Bonfim e Tânia Bonfim (Dr. Júlio), ambos no contato de Tânia.
Uso: uv run python scripts/insert_hugo_tania_oneoff.py
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

NUMBER = "5581988593028"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

TANIA_ID = "d03032d5-d814-4fa5-bc2f-ad908e461db8"

HUGO = {
    "number": NUMBER,
    "name": "Tânia Regina Bonfim de Oliveira",
    "patient_name": "Hugo Bonfim Carneiro da Cunha",
    "birth_date": "17/09/2007",
    "age": 18,
    "email": "taniabonfim@gmail.com",
    "guardian_name": "Tânia Regina Bonfim de Oliveira",
    "guardian_relationship": "mãe",
    "doctor_id": DOCTOR_ID_JULIO,
    "is_patient": True,
    "active": True,
}

# Tânia já tem registro — apenas atualiza os campos faltantes
TANIA_UPDATE = {
    "name": "Tânia Regina Bonfim de Oliveira",
    "patient_name": "Tânia Regina Bonfim de Oliveira",
    "birth_date": "15/08/1967",
    "age": 58,
    "email": "taniabonfim@gmail.com",
    "patient_cpf": "535.843.174-87",
    "doctor_id": DOCTOR_ID_JULIO,
    "is_patient": True,
    "active": True,
}

HUGO_ID = "1333f52b-7517-4d65-a2a2-30ebfe8aad93"
HUGO_UPDATE = {
    "guardian_cpf": "535.843.174-87",
}


async def main():
    from app.database import get_supabase, get_users_by_phone

    existing = await get_users_by_phone(NUMBER)
    print(f"Pacientes existentes para {NUMBER}:")
    for u in existing:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")

    client = await get_supabase()

    # Atualiza Tânia (patient_cpf na linha dela)
    await client.from_("users").update(TANIA_UPDATE).eq("id", TANIA_ID).execute()
    print(f"\nAtualizado: Tânia Regina Bonfim de Oliveira (id={TANIA_ID})")

    # Atualiza Hugo com guardian_cpf de Tânia
    await client.from_("users").update(HUGO_UPDATE).eq("id", HUGO_ID).execute()
    print(f"Atualizado: Hugo Bonfim Carneiro da Cunha — guardian_cpf (id={HUGO_ID})")

    # Verifica se Hugo já foi inserido na execução anterior
    rows = await get_users_by_phone(NUMBER)
    hugo_exists = any(
        (r.get("patient_name") or "").lower() == "hugo bonfim carneiro da cunha"
        for r in rows
    )
    if hugo_exists:
        print("Hugo já existe — pulando inserção.")
    else:
        result = await client.from_("users").insert(HUGO).execute()
        inserted = (result.data or [{}])[0]
        print(f"Inserido: Hugo Bonfim Carneiro da Cunha (id={inserted.get('id')})")

    updated = await get_users_by_phone(NUMBER)
    print(f"\nPacientes após operações:")
    for u in updated:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")


asyncio.run(main())
