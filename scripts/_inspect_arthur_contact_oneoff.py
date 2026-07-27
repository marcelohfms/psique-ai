import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    patients = (await client.from_("patients").select("*").ilike("name", "%Arthur%").execute()).data
    print("=== Pacientes Arthur ===")
    for p in patients:
        print(p["id"], p.get("name"), "returning:", p.get("is_returning_patient"))

    contacts = (await client.from_("contacts").select("*").execute()).data
    target_variants = ["5581995821211", "558195821211", "81995821211", "8195821211"]
    matches = [c for c in contacts if any(v in (c.get("phone") or "") for v in target_variants) or (c.get("phone") or "").replace("+","") in target_variants]
    print("\n=== Contatos com esse telefone (variantes) ===")
    for c in matches:
        print(c)

    print("\n=== patient_contacts para cada Arthur ===")
    for p in patients:
        pcs = (await client.from_("patient_contacts").select("*, contacts(*)").eq("patient_id", p["id"]).execute()).data
        print(f"-- paciente {p['id']} ({p.get('name')}) --")
        for pc in pcs:
            print(" ", pc)

asyncio.run(main())
