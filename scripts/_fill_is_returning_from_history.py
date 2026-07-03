"""Preenche is_returning_patient para pacientes com consulta futura e o campo nulo.

Regra: se o paciente tem ALGUMA consulta com status 'completed' no histórico →
retornante (True). Caso contrário → novo (False). O consultation_type da consulta
futura ('primeira_consulta'/'acompanhamento') é exibido apenas como corroboração.

Dry:   uv run python scripts/_fill_is_returning_from_history.py
Apply: uv run python scripts/_fill_is_returning_from_history.py --apply
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

APPLY = "--apply" in sys.argv


async def _fetch_all(client, table, columns):
    rows, start = [], 0
    while True:
        r = (await client.from_(table).select(columns).range(start, start + 999).execute()).data
        rows += r
        if len(r) < 1000:
            break
        start += 1000
    return rows


async def main():
    from supabase import acreate_client
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    now_iso = datetime.now(timezone.utc).isoformat()

    # Agendamentos futuros
    upcoming = (
        await client.from_("appointments")
        .select("patient_id, start_time, consultation_type")
        .in_("status", ["scheduled", "pending_reschedule"])
        .gte("end_time", now_iso)
        .order("start_time")
        .execute()
    ).data

    upcoming_by_patient = {}
    for a in upcoming:
        upcoming_by_patient.setdefault(a["patient_id"], a)  # primeiro (mais cedo)

    # Todos os appointments (para contar 'completed' por paciente)
    all_appts = await _fetch_all(client, "appointments", "patient_id, status")
    completed_count = {}
    for a in all_appts:
        if a["status"] == "completed":
            completed_count[a["patient_id"]] = completed_count.get(a["patient_id"], 0) + 1

    patients = await _fetch_all(client, "patients", "id, name, is_returning_patient")
    pat_by_id = {p["id"]: p for p in patients}

    # Alvo: paciente com consulta futura E is_returning_patient nulo
    targets = []
    for pid in upcoming_by_patient:
        p = pat_by_id.get(pid)
        if p and p.get("is_returning_patient") is None:
            targets.append(pid)

    print(f"Pacientes com consulta futura e is_returning_patient nulo: {len(targets)}\n")
    decisions = []
    for pid in targets:
        p = pat_by_id[pid]
        appt = upcoming_by_patient[pid]
        done = completed_count.get(pid, 0)
        decision = True if done > 0 else False
        decisions.append((pid, p.get("name"), appt["start_time"][:16].replace("T", " "),
                          appt.get("consultation_type"), done, decision))

    print(f"{'Paciente':45} {'consulta':16} {'tipo_futura':16} {'concl.':6} -> is_returning")
    for pid, nome, when, ctype, done, dec in sorted(decisions, key=lambda x: x[2]):
        print(f"{(nome or '?'):45} {when:16} {str(ctype):16} {done:^6} -> {dec}")

    n_true = sum(1 for *_, d in decisions if d)
    n_false = len(decisions) - n_true
    print(f"\nResumo: {n_true} retornantes (True), {n_false} novos (False)")

    if not APPLY:
        print("\n[DRY RUN] Nada gravado. Rode com --apply para aplicar.")
        return

    print("\n[APPLY] Atualizando...")
    for pid, nome, *_rest, dec in decisions:
        await client.from_("patients").update({"is_returning_patient": dec}).eq("id", pid).execute()
    print(f"Atualizados {len(decisions)} pacientes.")


asyncio.run(main())
