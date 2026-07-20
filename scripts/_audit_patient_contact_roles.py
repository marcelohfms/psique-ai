"""Audita patient_contacts: lista pares (paciente, contato) que NÃO têm os três
roles (agendamento, financeiro, consulta). Foco especial em contatos NÃO-self
(terceiros que agendam/pagam), onde a falta de 'financeiro' quebra a cobrança
automática — mas mostra todos.
"""
import asyncio
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

ALL_ROLES = {"agendamento", "financeiro", "consulta"}


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # paginação simples
    rows = []
    page = 0
    while True:
        r = await (
            client.from_("patient_contacts")
            .select("patient_id, contact_id, role, is_self, relationship, "
                    "patients(name), contacts(name, phone)")
            .range(page * 1000, page * 1000 + 999)
            .execute()
        )
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < 1000:
            break
        page += 1

    groups = defaultdict(lambda: {"roles": {}, "meta": None})
    for row in rows:
        key = (row["patient_id"], row["contact_id"])
        groups[key]["roles"][row["role"]] = row.get("is_self")
        groups[key]["meta"] = row

    incomplete = []
    for key, info in groups.items():
        present = set(info["roles"].keys())
        missing = ALL_ROLES - present
        if missing:
            incomplete.append((key, present, missing, info["meta"]))

    print(f"Total de pares (paciente,contato): {len(groups)}")
    print(f"Pares INCOMPLETOS (< 3 roles): {len(incomplete)}\n")

    # ordena: não-self primeiro (mais críticos), depois por nome do paciente
    def sort_key(item):
        meta = item[3]
        is_self_any = any(v for v in groups[item[0]]["roles"].values())
        return (is_self_any, (meta.get("patients") or {}).get("name") or "")

    for key, present, missing, meta in sorted(incomplete, key=sort_key):
        pat = (meta.get("patients") or {}).get("name") or "—"
        con = meta.get("contacts") or {}
        cname = con.get("name") or "—"
        cphone = con.get("phone") or "—"
        is_self = any(groups[key]["roles"].values())
        flag = "" if is_self else "  ⚠️ NÃO-SELF (terceiro)"
        crit = "  🔴 falta financeiro" if "financeiro" in missing else ""
        print(f"• {pat}  ←  {cname} ({cphone}){flag}")
        print(f"    tem: {sorted(present)} | falta: {sorted(missing)}{crit}")
        print(f"    patient_id={key[0]}  contact_id={key[1]}")


asyncio.run(main())
