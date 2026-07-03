"""Read-only inspection: patients with null patient_cpf and their guardian contacts."""
import asyncio
import os
from collections import Counter

from dotenv import load_dotenv
load_dotenv()


async def main():
    from supabase import acreate_client
    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    pats = (await client.table("patients").select("id, name, patient_cpf").execute()).data
    total = len(pats)
    null_cpf = [p for p in pats if not p.get("patient_cpf")]
    print(f"Total patients: {total}")
    print(f"Patients with null/empty patient_cpf: {len(null_cpf)}")

    # patient_contacts schema
    pc_sample = (await client.table("patient_contacts").select("*").limit(3).execute()).data
    print("\npatient_contacts sample columns:", list(pc_sample[0].keys()) if pc_sample else "none")
    for row in pc_sample:
        print("  ", row)

    # For null-cpf patients, look at their links
    rel_counter = Counter()
    has_guardian_cpf = 0
    no_link = 0
    self_only = 0
    for p in null_cpf:
        links = (await client.table("patient_contacts")
                 .select("contact_id, is_self, relationship")
                 .eq("patient_id", p["id"]).execute()).data
        if not links:
            no_link += 1
            continue
        guardians = [l for l in links if not l.get("is_self")]
        if not guardians:
            self_only += 1
            continue
        for g in guardians:
            rel_counter[g.get("relationship")] += 1
            c = (await client.table("contacts").select("cpf, name")
                 .eq("id", g["contact_id"]).execute()).data
            if c and c[0].get("cpf"):
                has_guardian_cpf += 1
                break

    print(f"\nNull-cpf patients with NO patient_contacts link: {no_link}")
    print(f"Null-cpf patients with only is_self link (no guardian): {self_only}")
    print(f"Null-cpf patients with at least one guardian that HAS a cpf: {has_guardian_cpf}")
    print(f"\nGuardian relationship values among null-cpf patients: {dict(rel_counter)}")


asyncio.run(main())
