"""Migração one-off: copia (is_self, relationship) da linha `agendamento`
(íntegra) para as linhas `consulta`/`financeiro` de cada par (paciente, contato).

Uso:
  uv run python scripts/_migrate_marker_from_agendamento.py         # dry-run
  uv run python scripts/_migrate_marker_from_agendamento.py --apply # aplica
"""
import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

from app.supabase_client import get_supabase


def decide_updates(rows: list[dict]) -> list[dict]:
    """Dado TODAS as linhas patient_contacts de UM par (paciente, contato),
    devolve as linhas de consulta/financeiro que precisam ser alinhadas ao
    agendamento. Vazio se não há agendamento ou já está consistente."""
    ag = next((r for r in rows if r.get("role") == "agendamento"), None)
    if ag is None:
        return []
    target_self = bool(ag.get("is_self"))
    target_rel = ag.get("relationship")
    updates = []
    for r in rows:
        if r.get("role") == "agendamento":
            continue
        if bool(r.get("is_self")) != target_self or r.get("relationship") != target_rel:
            updates.append({
                "patient_id": r["patient_id"], "contact_id": r["contact_id"],
                "role": r["role"], "is_self": target_self, "relationship": target_rel,
            })
    return updates


async def _fetch_all(client):
    rows, start, page = [], 0, 1000
    while True:
        res = await (
            client.from_("patient_contacts")
            .select("patient_id, contact_id, role, is_self, relationship")
            .range(start, start + page - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


async def main(apply: bool):
    client = await get_supabase()
    rows = await _fetch_all(client)
    pairs: dict = {}
    for r in rows:
        pairs.setdefault((r["patient_id"], r["contact_id"]), []).append(r)

    all_updates = []
    for pair_rows in pairs.values():
        all_updates.extend(decide_updates(pair_rows))

    print(f"linhas a alinhar: {len(all_updates)}")
    for u in all_updates:
        print(f"  {u['patient_id']} / {u['contact_id']} role={u['role']} "
              f"-> is_self={u['is_self']} rel={u['relationship']!r}")

    if not apply:
        print("\n(dry-run — nada aplicado; rode com --apply para gravar)")
        return

    for u in all_updates:
        await (
            client.from_("patient_contacts")
            .update({"is_self": u["is_self"], "relationship": u["relationship"]})
            .eq("patient_id", u["patient_id"])
            .eq("contact_id", u["contact_id"])
            .eq("role", u["role"])
            .execute()
        )
    print(f"\naplicado: {len(all_updates)} linhas")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
