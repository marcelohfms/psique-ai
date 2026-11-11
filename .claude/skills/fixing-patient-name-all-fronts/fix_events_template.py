"""TEMPLATE — Frente 3: corrige o nome do paciente nos eventos de log.

Copie para scripts/_fix_<nome>_eventos_oneoff.py e preencha as constantes.
Troca metadata.patient_name == OLD_FULL por NEW_FULL e o sufixo "— <1º nome do responsável>"
em attrs.proxima_consulta por "— <1º nome do paciente>", guardando o original em
metadata.manual_correction. Dry-run por padrão; --live efetiva.
"""
import asyncio
import json
import sys

import scripts._dnsfix  # noqa: F401
from dotenv import load_dotenv

load_dotenv()

LIVE = "--live" in sys.argv

# --- PREENCHER com o diagnóstico ---
PHONE = "55819XXXXXXXX"
OLD_FULL = "Nome Do Responsavel"
NEW_FULL = "Nome Completo Do Paciente"
OLD_FIRST = "Responsavel"     # 1º nome como aparece no sufixo "— ..." de proxima_consulta
NEW_FIRST = "Paciente"        # 1º nome do paciente
NOTE = "AAAA-MM-DD: nome do paciente corrigido de <responsável> para <paciente>; bug do nome do contato copiado pro paciente"
# ------------------------------------


async def main():
    from app.database import get_supabase

    c = await get_supabase()
    ev = (await c.table("events").select("id,created_at,event_type,metadata")
          .eq("phone", PHONE).order("created_at").execute()).data

    planned = []
    for e in ev:
        md = dict(e.get("metadata") or {})
        changed = False
        if md.get("patient_name") == OLD_FULL:
            md["patient_name"] = NEW_FULL
            changed = True
        attrs = md.get("attrs")
        if isinstance(attrs, dict):
            pc = attrs.get("proxima_consulta", "")
            if pc.endswith(f"— {OLD_FIRST}"):
                attrs = dict(attrs)
                attrs["proxima_consulta"] = pc[: -len(f"— {OLD_FIRST}")] + f"— {NEW_FIRST}"
                md["attrs"] = attrs
                changed = True
        if changed:
            md["manual_correction"] = NOTE
            planned.append((e["id"], e["event_type"], md))

    print(f"{len(planned)} evento(s) para corrigir:")
    for eid, et, md in planned:
        print(" ", eid, et, "->", json.dumps(md, ensure_ascii=False)[:220])

    if not LIVE:
        print("\n[DRY-RUN] nada alterado. Rode com --live.")
        return

    for eid, et, md in planned:
        await c.table("events").update({"metadata": md}).eq("id", eid).execute()
        print("corrigido:", eid, et)


asyncio.run(main())
