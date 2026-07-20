import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581979087152"


async def main():
    from app.database import get_supabase, _phone_variants
    from app.patients import get_contact_by_phone
    client = await get_supabase()

    print(f"Variantes testadas: {_phone_variants(PHONE)}\n")

    contact = await get_contact_by_phone(PHONE)
    if not contact:
        print("Nenhum contato encontrado.")
        # tenta por sufixo de 8 dígitos (dígitos transpostos / legados)
        suffix = PHONE[-8:]
        res = await client.from_("contacts").select("*").ilike("phone", f"%{suffix}").execute()
        print(f"Busca por sufixo %{suffix}: {len(res.data or [])} resultado(s)")
        for c in (res.data or []):
            print(f"  phone={c['phone']} | name={c.get('name')} | id={c['id']}")
        return

    print("=== CONTACT ===")
    for k, v in contact.items():
        print(f"  {k}: {v}")

    print("\n=== PATIENT_CONTACTS + PATIENTS ===")
    resp = await (
        client.from_("patient_contacts")
        .select("is_self, relationship, role, patients(*)")
        .eq("contact_id", contact["id"])
        .execute()
    )
    for row in (resp.data or []):
        p = row.get("patients") or {}
        print(f"\n  role={row.get('role')} | is_self={row.get('is_self')} | relationship={row.get('relationship')}")
        print(f"  paciente: {p.get('full_name') or p.get('name')} | id={p.get('id')} | is_returning={p.get('is_returning_patient')} | active={p.get('active')}")
        print(f"  nascimento={p.get('birth_date')} | cpf={p.get('cpf')} | email={p.get('email')}")


asyncio.run(main())
