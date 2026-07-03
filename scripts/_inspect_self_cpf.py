"""Read-only: for null-cpf patients, how many have an is_self contact WITH a cpf?"""
import asyncio
import os

from dotenv import load_dotenv
load_dotenv()


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
    links = await _fetch_all(client, "patient_contacts",
                             "patient_id, contact_id, is_self, relationship")
    contacts = await _fetch_all(client, "contacts", "id, cpf")
    cpf_by_contact = {c["id"]: c.get("cpf") for c in contacts}

    null_patients = [p for p in patients if not p.get("patient_cpf")]
    null_ids = {p["id"] for p in null_patients}

    self_cpf = {}
    for l in links:
        if not l.get("is_self"):
            continue
        if l["patient_id"] not in null_ids:
            continue
        cpf = cpf_by_contact.get(l["contact_id"])
        if cpf:
            self_cpf.setdefault(l["patient_id"], cpf)

    print(f"Null-cpf patients: {len(null_patients)}")
    print(f"Of those, is_self contact HAS a cpf: {len(self_cpf)}")


asyncio.run(main())
