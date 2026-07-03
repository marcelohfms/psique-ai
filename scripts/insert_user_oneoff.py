"""
One-off: insere segundo paciente para o número 5581988417858.
Uso: uv run python scripts/insert_user_oneoff.py
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

NUMBER = "5581988417858"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"


async def main():
    from app.database import get_supabase, get_users_by_phone

    existing = await get_users_by_phone(NUMBER)
    print(f"Pacientes existentes para {NUMBER}:")
    for u in existing:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")

    client = await get_supabase()
    result = await client.from_("users").insert({
        "number": NUMBER,
        "name": "Janaina Mirses S C Costa",
        "patient_name": "Isadora de Sousa Costa",
        "age": 14,
        "birth_date": "16/11/2011",
        "doctor_id": DOCTOR_ID_JULIO,
        "is_patient": True,
        "email": "jmirses00@gmail.com",
        "guardian_name": "Janaina Mirses S C Costa",
        "guardian_relationship": "mãe",
        "active": True,
    }).execute()

    print(f"\nInserido: {result.data}")

    updated = await get_users_by_phone(NUMBER)
    print(f"\nPacientes após inserção:")
    for u in updated:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")


asyncio.run(main())
