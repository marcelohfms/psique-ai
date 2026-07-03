"""
One-off: cadastra Lara Pinto de Carvalho (Dr. Júlio), contato da mãe Vania Carvalho.
Uso: uv run python scripts/insert_lara_oneoff.py
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

NUMBER = "5581998696027"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

LARA = {
    "number": NUMBER,
    "name": "Vania Carvalho",
    "patient_name": "Lara Pinto de Carvalho",
    "birth_date": "25/03/2009",
    "age": 17,
    "email": "vrcarvalho1962@gmail.com",
    "guardian_name": "Vania Carvalho",
    "guardian_relationship": "mãe",
    "guardian_cpf": "342.665.504-72",
    "doctor_id": DOCTOR_ID_JULIO,
    "is_patient": True,
    "active": True,
}


async def main():
    from app.database import get_supabase, get_users_by_phone

    existing = await get_users_by_phone(NUMBER)
    print(f"Pacientes existentes para {NUMBER}:")
    for u in existing:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")

    client = await get_supabase()

    lara_row = next(
        (r for r in existing if (r.get("patient_name") or "").lower() == "lara pinto de carvalho"),
        None,
    )
    if lara_row:
        await client.from_("users").update(LARA).eq("id", lara_row["id"]).execute()
        print(f"\nAtualizado: Lara Pinto de Carvalho (id={lara_row['id']})")
    else:
        result = await client.from_("users").insert(LARA).execute()
        inserted = (result.data or [{}])[0]
        print(f"\nInserido: {inserted.get('patient_name')} (id={inserted.get('id')})")

    updated = await get_users_by_phone(NUMBER)
    print(f"\nPacientes após inserção:")
    for u in updated:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")


asyncio.run(main())
