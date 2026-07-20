import asyncio
from dotenv import load_dotenv
load_dotenv()

CONTACT_ID = "e7b60b43-29e8-4cb1-94ba-2930e192e2ca"


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    pc_resp = await (
        client.from_("patient_contacts")
        .select("is_self, relationship, role, patients(*)")
        .eq("contact_id", CONTACT_ID)
        .execute()
    )
    pc_rows = pc_resp.data or []
    print(f"=== patient_contacts: {len(pc_rows)} ===")
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
