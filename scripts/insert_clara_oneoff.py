"""
One-off: corrige registro de Clara Van Der Linden Madruga (contato próprio)
e adiciona segundo registro com contato provável da mãe Dilenia Van Der Linden.
Uso: uv run python scripts/insert_clara_oneoff.py
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

NUMBER_CLARA = "5581999249242"
NUMBER_MAE = "5581982246297"
DOCTOR_ID_JULIO = "d5baa58b-a788-4f40-b8c0-512c189150be"

CLARA_PROPRIO = {
    "number": NUMBER_CLARA,
    "name": "Clara Van Der Linden Madruga",
    "patient_name": "Clara Van Der Linden Madruga",
    "birth_date": "08/09/2009",
    "age": 16,
    "email": "dilenia.linden@otis.com",
    "guardian_name": "Dilenia Van Der Linden",
    "guardian_relationship": "mãe",
    "guardian_cpf": None,
    "patient_cpf": "754.862.704-10",
    "doctor_id": DOCTOR_ID_JULIO,
    "is_patient": True,
    "active": True,
}

CLARA_MAE = {
    "number": NUMBER_MAE,
    "name": "Dilenia Van Der Linden",
    "patient_name": "Clara Van Der Linden Madruga",
    "birth_date": "08/09/2009",
    "age": 16,
    "email": "dilenia.linden@otis.com",
    "guardian_name": "Dilenia Van Der Linden",
    "guardian_relationship": "mãe",
    "guardian_cpf": None,
    "patient_cpf": "754.862.704-10",
    "doctor_id": DOCTOR_ID_JULIO,
    "is_patient": True,
    "active": True,
}


async def main():
    from app.database import get_supabase, get_users_by_phone

    client = await get_supabase()

    # Corrige registro existente (contato da própria Clara)
    existing_clara = await get_users_by_phone(NUMBER_CLARA)
    clara_row = next(
        (r for r in existing_clara if (r.get("patient_name") or "").lower() == "clara van der linden madruga"),
        None,
    )
    if clara_row:
        await client.from_("users").update(CLARA_PROPRIO).eq("id", clara_row["id"]).execute()
        print(f"Atualizado contato próprio: Clara (id={clara_row['id']})")
    else:
        result = await client.from_("users").insert(CLARA_PROPRIO).execute()
        inserted = (result.data or [{}])[0]
        print(f"Inserido contato próprio: Clara (id={inserted.get('id')})")

    # Insere/atualiza registro com contato da mãe
    existing_mae = await get_users_by_phone(NUMBER_MAE)
    mae_row = next(
        (r for r in existing_mae if (r.get("patient_name") or "").lower() == "clara van der linden madruga"),
        None,
    )
    if mae_row:
        await client.from_("users").update(CLARA_MAE).eq("id", mae_row["id"]).execute()
        print(f"Atualizado contato da mãe: Clara (id={mae_row['id']})")
    else:
        result = await client.from_("users").insert(CLARA_MAE).execute()
        inserted = (result.data or [{}])[0]
        print(f"Inserido contato da mãe: Clara (id={inserted.get('id')})")

    # Mostra estado final
    for number in [NUMBER_CLARA, NUMBER_MAE]:
        rows = await get_users_by_phone(number)
        print(f"\nRegistros para {number}:")
        for u in rows:
            print(f"  - {u.get('patient_name') or u.get('name')} | contato: {u.get('name')} (id={u['id']})")


asyncio.run(main())
