"""
One-off: cadastra Mariana, Manuela e Miguel de França Ramos (filhos de Rebeka França).
Uso: uv run python scripts/insert_rebeka_filhos_oneoff.py
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

NUMBER = "5581999995736"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

PACIENTES = [
    {
        "number": NUMBER,
        "name": "Rebeka França",
        "patient_name": "Mariana de França Ramos",
        "birth_date": "09/12/2008",
        "age": 17,
        "email": "rebekafranca27@gmail.com",
        "guardian_name": "Rebeka França",
        "guardian_relationship": "mãe",
        "doctor_id": DOCTOR_ID_JULIO,
        "is_patient": True,
        "active": True,
    },
    {
        "number": NUMBER,
        "name": "Rebeka França",
        "patient_name": "Manuela de França Ramos",
        "birth_date": "09/12/2008",
        "age": 17,
        "email": "rebekafranca27@gmail.com",
        "guardian_name": "Rebeka França",
        "guardian_relationship": "mãe",
        "doctor_id": DOCTOR_ID_JULIO,
        "is_patient": True,
        "active": True,
    },
    {
        "number": NUMBER,
        "name": "Rebeka França",
        "patient_name": "Miguel de França Ramos",
        "birth_date": "10/02/2012",
        "age": 14,
        "email": "rebekafranca27@gmail.com",
        "guardian_name": "Rebeka França",
        "guardian_relationship": "mãe",
        "doctor_id": DOCTOR_ID_JULIO,
        "is_patient": True,
        "active": True,
    },
]


async def main():
    from app.database import get_supabase, get_users_by_phone

    existing = await get_users_by_phone(NUMBER)
    print(f"Pacientes existentes para {NUMBER}:")
    for u in existing:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")

    client = await get_supabase()

    for paciente in PACIENTES:
        nome = paciente["patient_name"].lower()
        row = next(
            (r for r in existing if (r.get("patient_name") or "").lower() == nome),
            None,
        )
        if row:
            await client.from_("users").update(paciente).eq("id", row["id"]).execute()
            print(f"\nAtualizado: {paciente['patient_name']} (id={row['id']})")
        else:
            result = await client.from_("users").insert(paciente).execute()
            inserted = (result.data or [{}])[0]
            print(f"\nInserido: {inserted.get('patient_name')} (id={inserted.get('id')})")

    updated = await get_users_by_phone(NUMBER)
    print(f"\nPacientes após operação:")
    for u in updated:
        print(f"  - {u.get('patient_name') or u.get('name')} (id={u['id']})")


asyncio.run(main())
