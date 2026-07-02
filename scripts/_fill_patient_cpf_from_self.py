"""Fill patients.patient_cpf from the patient's OWN (is_self) contact CPF.

For null-cpf patients linked via patient_contacts.is_self = true, copy the
contact's cpf into patient_cpf.

Run dry:   uv run python scripts/_fill_patient_cpf_from_self.py
Run apply: uv run python scripts/_fill_patient_cpf_from_self.py --apply
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

APPLY = "--apply" in sys.argv


async def _fetch_all(client, table, columns):
    rows, start, page = [], 0, 1000
    while True:
        resp = (await client.table(table).select(columns)
                .range(start, start + page - 1).execute())
        rows.extend(resp.data)
        if len(resp.data) < page:
            break
        start += page
    return rows


async def main():
    from supabase import acreate_client
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    patients = await _fetch_all(client, "patients", "id, name, patient_cpf")
    links = await _fetch_all(client, "patient_contacts", "patient_id, contact_id, is_self")
    contacts = await _fetch_all(client, "contacts", "id, cpf")
    cpf_by_contact = {c["id"]: c.get("cpf") for c in contacts}

    null_patients = [p for p in patients if not p.get("patient_cpf")]
    null_by_id = {p["id"]: p for p in null_patients}

    self_cpf = {}
    for l in links:
        if not l.get("is_self") or l["patient_id"] not in null_by_id:
            continue
        cpf = cpf_by_contact.get(l["contact_id"])
        if cpf:
            self_cpf.setdefault(l["patient_id"], cpf)

    print(f"Patients with null patient_cpf: {len(null_patients)}")
    print(f"Will fill from own (is_self) contact: {len(self_cpf)}")
    print(f"Will REMAIN null afterwards: {len(null_patients) - len(self_cpf)}")
    print()
    for pid, cpf in self_cpf.items():
        print(f"  {null_by_id[pid]['name']!r:40} -> {cpf}")

    if not APPLY:
        print("\n[DRY RUN] Re-run with --apply to commit.")
        return

    print(f"\n[APPLY] Updating {len(self_cpf)} patients...")
    for pid, cpf in self_cpf.items():
        await client.table("patients").update({"patient_cpf": cpf}).eq("id", pid).execute()
    print(f"Done. Patients STILL without CPF: {len(null_patients) - len(self_cpf)}")


asyncio.run(main())
