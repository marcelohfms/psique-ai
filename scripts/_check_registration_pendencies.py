"""Lista pacientes COM CONSULTA AGENDADA que têm pendência cadastral.

Para cada appointment futuro (status scheduled/pending_reschedule, end_time >= agora),
reconstrói o cadastro do paciente (patients + patient_contacts + contacts) e aponta
quais campos obrigatórios estão faltando, usando a mesma regra de
is_registration_complete.

Run: uv run python scripts/_check_registration_pendencies.py
"""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from app.database import _legacy_user_dict, is_registration_complete, DOCTOR_IDS


async def _fetch_all(client, table, columns, **eq):
    rows, start, page = [], 0, 1000
    while True:
        q = client.from_(table).select(columns)
        q = q.range(start, start + page - 1)
        resp = await q.execute()
        rows.extend(resp.data)
        if len(resp.data) < page:
            break
        start += page
    return rows


def _missing_fields(u: dict) -> list[str]:
    """Mesma lógica de is_registration_complete, mas retorna a lista de pendências."""
    missing = []
    labels = {
        "name": "nome do contato",
        "email": "e-mail",
        "birth_date": "data de nascimento",
        "doctor_id": "médico",
    }
    for f in ("name", "email", "birth_date", "doctor_id"):
        if not u.get(f):
            missing.append(labels[f])
    if u.get("is_patient") is None:
        missing.append("tipo (próprio/terceiro)")
    age = u.get("age")
    _is_julio_minor = (age is not None and age < 18
                       and u.get("doctor_id") == DOCTOR_IDS.get("julio"))
    if _is_julio_minor and u.get("is_returning_patient") is None:
        missing.append("status (novo/retornante)")
    if u.get("is_patient") is False and not u.get("patient_name"):
        missing.append("nome do paciente")
    age = u.get("age")
    if age is not None and age < 18:
        if not u.get("guardian_name"):
            missing.append("nome do responsável")
        if not u.get("guardian_relationship"):
            missing.append("relação do responsável")
        if u.get("is_returning_patient") is False and not u.get("guardian_cpf"):
            missing.append("CPF do responsável")
    return missing


async def main():
    from supabase import acreate_client
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    now_iso = datetime.now(timezone.utc).isoformat()

    # Agendamentos futuros/ativos
    appts = (
        await client.from_("appointments")
        .select("appointment_id, patient_id, start_time, end_time, status")
        .in_("status", ["scheduled", "pending_reschedule"])
        .gte("end_time", now_iso)
        .order("start_time")
        .execute()
    ).data

    # patient_id -> primeiro agendamento (para exibir data)
    patient_ids = []
    appt_by_patient = {}
    for a in appts:
        pid = a["patient_id"]
        if pid not in appt_by_patient:
            appt_by_patient[pid] = a
            patient_ids.append(pid)

    # Carrega tudo em massa
    patients = await _fetch_all(client, "patients", "*")
    pat_by_id = {p["id"]: p for p in patients}
    links = await _fetch_all(client, "patient_contacts",
                             "patient_id, contact_id, is_self, relationship, role")
    contacts = await _fetch_all(client, "contacts", "*")
    contact_by_id = {c["id"]: c for c in contacts}

    links_by_patient = {}
    for l in links:
        links_by_patient.setdefault(l["patient_id"], []).append(l)

    print(f"Agendamentos futuros (scheduled/pending_reschedule): {len(appts)}")
    print(f"Pacientes distintos com consulta agendada: {len(patient_ids)}\n")

    pendentes = []
    sem_vinculo = []
    for pid in patient_ids:
        patient = pat_by_id.get(pid)
        if not patient:
            sem_vinculo.append((pid, "paciente não encontrado na tabela patients"))
            continue
        plinks = links_by_patient.get(pid, [])
        if not plinks:
            sem_vinculo.append((patient.get("name") or pid, "sem patient_contacts"))
            continue
        # Escolhe o contato: prioriza is_self; senão, primeiro guardião
        self_link = next((l for l in plinks if l.get("is_self")), None)
        if self_link:
            link = self_link
            is_self = True
        else:
            link = plinks[0]
            is_self = False
        contact = contact_by_id.get(link["contact_id"]) or {}
        u = _legacy_user_dict(contact, patient, is_self, link.get("relationship"))

        if not is_registration_complete(u):
            miss = _missing_fields(u)
            appt = appt_by_patient[pid]
            when = appt["start_time"][:16].replace("T", " ")
            nome = patient.get("name") or u.get("patient_name") or "(sem nome)"
            pendentes.append((nome, when, miss, patient.get("phone") if patient.get("phone") else contact.get("phone")))

    print(f"=== PACIENTES COM PENDÊNCIA CADASTRAL: {len(pendentes)} ===\n")
    for nome, when, miss, phone in sorted(pendentes, key=lambda x: x[1]):
        print(f"• {nome}  —  consulta {when}  —  tel {phone or '?'}")
        print(f"    faltando: {', '.join(miss)}")

    if sem_vinculo:
        print(f"\n=== Sem vínculo/registro completo (revisar manualmente): {len(sem_vinculo)} ===")
        for ident, motivo in sem_vinculo:
            print(f"• {ident}: {motivo}")


asyncio.run(main())
