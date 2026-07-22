import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581988851971"


async def main():
    from app.database import get_supabase, _phone_variants

    client = await get_supabase()

    variants = _phone_variants(f"{PHONE}@s.whatsapp.net")
    print(f"variantes de telefone testadas: {variants}")

    contacts_all = []
    for v in variants:
        resp = await client.from_("contacts").select("*").eq("phone", f"{v}@s.whatsapp.net").execute()
        contacts_all.extend(resp.data or [])

    print(f"\n=== contacts encontrados: {len(contacts_all)} ===")
    for c in contacts_all:
        print(c)

    if not contacts_all:
        print("Nenhum contato encontrado para esse telefone.")
        return

    for contact in contacts_all:
        pc_resp = await (
            client.from_("patient_contacts")
            .select("is_self, relationship, role, patients(*)")
            .eq("contact_id", contact["id"])
            .execute()
        )
        pc_rows = pc_resp.data or []
        print(f"\n=== patient_contacts para contact_id={contact['id']}: {len(pc_rows)} ===")
        for row in pc_rows:
            patient = row.get("patients")
            if not patient:
                continue
            print(f"\n--- paciente: {patient.get('name')} (id={patient['id']}) ---")
            print(f"  is_self={row.get('is_self')} relationship={row.get('relationship')} role={row.get('role')}")
            for k, v in patient.items():
                print(f"  {k}: {v!r}")

            appts = await (
                client.from_("appointments")
                .select("*")
                .eq("patient_id", patient["id"])
                .order("start_time")
                .execute()
            )
            print(f"\n  agendamentos: {len(appts.data)}")
            for a in appts.data:
                print("   ", a)

asyncio.run(main())
