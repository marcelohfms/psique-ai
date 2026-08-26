"""AUDITORIA READ-ONLY: pacientes com o mesmo padrão da Daniela De Souza Passos
(patient_id=9be9c778-fba9-4f2a-95ea-0633902c26af).

Padrão: paciente ADULTO (idade >= 18) que tem contatos em role="consulta" mas
NENHUM deles está marcado is_self=True — e, dentre os contatos de "consulta",
existe um cujo telefone bate com um contato is_self=True em OUTRO papel
(ex.: "agendamento") do MESMO paciente. Isso prova que o número é do próprio
paciente mas está mal marcado (is_self=False) no papel de consulta, o que faz
get_reminder_contacts (app/patients.py) cair no fallback e mandar lembrete de
consulta para todo mundo (mãe, irmã, etc.) em vez de só para o paciente.

NÃO ALTERA NADA. Só lê e imprime.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from app.supabase_client import get_supabase
from app.patients import _compute_age


async def fetch_all(client, table, select, page_size=1000):
    """Pagina uma tabela inteira via range() para evitar limite default do supabase-py."""
    rows = []
    start = 0
    while True:
        res = (
            await client.from_(table)
            .select(select)
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


async def main():
    client = await get_supabase()

    print("Carregando patient_contacts (com contacts.phone)...")
    pc_rows = await fetch_all(
        client, "patient_contacts", "patient_id, role, is_self, contact_id, contacts(phone,name,active)"
    )
    print(f"  {len(pc_rows)} linhas em patient_contacts")

    print("Carregando patients (id, name, birth_date)...")
    patients = await fetch_all(client, "patients", "id, name, birth_date")
    patients_by_id = {p["id"]: p for p in patients}
    print(f"  {len(patients)} pacientes")

    # Agrupa patient_contacts por patient_id
    by_patient: dict[str, list[dict]] = {}
    for row in pc_rows:
        pid = row.get("patient_id")
        if not pid:
            continue
        by_patient.setdefault(pid, []).append(row)

    affected = []

    for pid, rows in by_patient.items():
        consulta_rows = [r for r in rows if r.get("role") == "consulta"]
        if not consulta_rows:
            continue

        # já existe algum is_self=True em role=consulta? então está OK.
        if any(r.get("is_self") for r in consulta_rows):
            continue

        # telefones is_self=True em QUALQUER outro papel deste paciente
        self_phones_other_roles = set()
        for r in rows:
            if r.get("role") != "consulta" and r.get("is_self"):
                c = r.get("contacts") or {}
                phone = c.get("phone")
                if phone:
                    self_phones_other_roles.add(phone)

        if not self_phones_other_roles:
            continue

        # algum contato de "consulta" (is_self=False) bate com um desses telefones?
        suspect_phone = None
        for r in consulta_rows:
            c = r.get("contacts") or {}
            phone = c.get("phone")
            if phone and phone in self_phones_other_roles:
                suspect_phone = phone
                break

        if not suspect_phone:
            continue

        patient = patients_by_id.get(pid)
        if not patient:
            continue

        age = _compute_age(patient.get("birth_date"))
        if age is None or age < 18:
            continue

        affected.append({
            "patient_id": pid,
            "name": patient.get("name"),
            "age": age,
            "phone": suspect_phone,
        })

    print("\n=== RESULTADO ===")
    if not affected:
        print("Nenhum paciente encontrado com o mesmo padrão.")
    else:
        print(f"{len(affected)} paciente(s) afetado(s):\n")
        for a in affected:
            print(f"- patient_id={a['patient_id']}  nome={a['name']!r}  idade={a['age']}  telefone_mal_marcado={a['phone']}")


asyncio.run(main())
