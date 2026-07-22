import asyncio
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase

    client = await get_supabase()

    # Pull all patients (paginate in case there are many).
    all_patients = []
    page_size = 1000
    offset = 0
    while True:
        res = await client.from_("patients").select(
            "id, name, birth_date, email, patient_cpf, created_at"
        ).range(offset, offset + page_size - 1).execute()
        if not res.data:
            break
        all_patients.extend(res.data)
        if len(res.data) < page_size:
            break
        offset += page_size

    print(f"Total patients: {len(all_patients)}")

    def norm_name(n):
        return " ".join((n or "").strip().lower().split())

    # Group by normalized name + birth_date
    groups = defaultdict(list)
    for p in all_patients:
        key = (norm_name(p.get("name")), p.get("birth_date"))
        groups[key].append(p)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1 and k[0]}

    print(f"\n=== Duplicate groups by name+birth_date: {len(dup_groups)} ===")
    for (name, bdate), plist in dup_groups.items():
        print(f"\n-- {name} | birth_date={bdate} --")
        for p in plist:
            print(f"   id={p['id']} created={p['created_at']} email={p.get('email')} cpf={p.get('patient_cpf')}")

    # Also group by CPF alone (in case birth_date differs but CPF matches - real dup)
    cpf_groups = defaultdict(list)
    for p in all_patients:
        cpf = p.get("patient_cpf")
        if cpf:
            cpf_groups[cpf].append(p)
    cpf_dups = {k: v for k, v in cpf_groups.items() if len(v) > 1}
    print(f"\n=== Duplicate groups by CPF: {len(cpf_dups)} ===")
    for cpf, plist in cpf_dups.items():
        print(f"\n-- CPF {cpf} --")
        for p in plist:
            print(f"   id={p['id']} name={p.get('name')} created={p['created_at']}")

asyncio.run(main())
