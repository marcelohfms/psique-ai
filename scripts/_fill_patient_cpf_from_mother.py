"""Fill patients.patient_cpf from the mother's (guardian) contact CPF.

For every patient with a null/empty patient_cpf, look up a linked guardian
contact (patient_contacts.is_self = false) whose relationship is 'mãe' and copy
that contact's cpf into the patient's patient_cpf.

Run dry:    uv run python scripts/_fill_patient_cpf_from_mother.py
Run apply:  uv run python scripts/_fill_patient_cpf_from_mother.py --apply
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

APPLY = "--apply" in sys.argv
# Whether to fall back to any guardian (pai/tia/etc.) when there is no 'mãe'.
ANY_GUARDIAN = "--any-guardian" in sys.argv


async def _fetch_all(client, table, columns):
    """Page through a table (Supabase caps at 1000 rows per request)."""
    rows, start, page = [], 0, 1000
    while True:
        resp = (await client.table(table).select(columns)
                .range(start, start + page - 1).execute())
        batch = resp.data
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


async def main():
    from supabase import acreate_client
    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    patients = await _fetch_all(client, "patients", "id, name, patient_cpf")
    links = await _fetch_all(client, "patient_contacts",
                             "patient_id, contact_id, is_self, relationship")
    contacts = await _fetch_all(client, "contacts", "id, name, cpf")

    cpf_by_contact = {c["id"]: c.get("cpf") for c in contacts}

    # patient_id -> chosen guardian cpf
    mother_cpf: dict[str, str] = {}
    any_guardian_cpf: dict[str, str] = {}
    for l in links:
        if l.get("is_self"):
            continue
        cpf = cpf_by_contact.get(l["contact_id"])
        if not cpf:
            continue
        pid = l["patient_id"]
        if l.get("relationship") == "mãe":
            mother_cpf.setdefault(pid, cpf)
        any_guardian_cpf.setdefault(pid, cpf)

    source = any_guardian_cpf if ANY_GUARDIAN else mother_cpf

    null_patients = [p for p in patients if not p.get("patient_cpf")]
    to_update = [(p, source[p["id"]]) for p in null_patients if p["id"] in source]

    print(f"Total patients:                 {len(patients)}")
    print(f"Patients with null patient_cpf: {len(null_patients)}")
    src_label = "ANY guardian" if ANY_GUARDIAN else "mother (mãe) only"
    print(f"Source: {src_label}")
    print(f"Will fill patient_cpf for:      {len(to_update)} patients")
    print(f"Will REMAIN null afterwards:    {len(null_patients) - len(to_update)}")
    print()
    for p, cpf in to_update:
        print(f"  {p['name']!r:40} -> {cpf}")

    if not APPLY:
        print("\n[DRY RUN] Nothing written. Re-run with --apply to commit.")
        return

    print(f"\n[APPLY] Updating {len(to_update)} patients...")
    done = 0
    for p, cpf in to_update:
        await client.table("patients").update({"patient_cpf": cpf}).eq("id", p["id"]).execute()
        done += 1
    print(f"Updated {done} patients.")
    print(f"Patients STILL without CPF: {len(null_patients) - done}")


asyncio.run(main())
