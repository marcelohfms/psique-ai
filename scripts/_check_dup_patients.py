import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("patients").select("id, name, birth_date, doctor_id, created_at, legacy_user_id").execute()
    
    seen = {}
    dups = []
    for p in res.data:
        key = (p["name"], p["birth_date"])
        if key in seen:
            dups.append((seen[key], p))
        else:
            seen[key] = p

    if not dups:
        print("Nenhum duplicado encontrado.")
    for a, b in dups:
        print(f"\n=== DUPLICADO: {a['name']} / {a['birth_date']} ===")
        print(f"  A: id={a['id']} created={a['created_at']} legacy={a['legacy_user_id']} doctor={a['doctor_id']}")
        print(f"  B: id={b['id']} created={b['created_at']} legacy={b['legacy_user_id']} doctor={b['doctor_id']}")

asyncio.run(main())
