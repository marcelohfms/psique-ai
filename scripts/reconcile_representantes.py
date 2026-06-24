"""Reconcilia os representantes (não-pacientes) para contatos soltos.

Para cada representante:
  - Se há um paciente no banco com esse contato: apaga o registro de `patients`
    + os vínculos `patient_contacts`, MANTÉM o `contact`, e seta manual_hold=true.
    Guarda: só apaga o paciente se o contato não servir a outro paciente real.
  - Se não há contato: cria um contact solto com manual_hold=true.

Uso:
    uv run python scripts/reconcile_representantes.py          # dry-run
    uv run python scripts/reconcile_representantes.py --exec   # aplica
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.database import get_supabase
from app.patients import normalize_phone

# nome (referência) -> número informado
REPS = {
    "Tássio Medeiros": "5581997556159",
    "Raísa Lima": "5581986215099",
    "João Alexandre": "5581996590590",
    "Emerson": "5581999940120",
    "Luísa Almeida": "5581981579151",
    "Julio Barbosa": "5581997358795",
    "Joanna Lira": "5581986038837",
    "Morgana Araújo": "5581995293210",
}


async def main(dry_run: bool) -> None:
    client = await get_supabase()
    for nome, raw in REPS.items():
        phone = normalize_phone(raw)
        existing = (await client.from_("contacts").select("id").eq("phone", phone).execute()).data or []
        if existing:
            cid = existing[0]["id"]
            links = (await client.from_("patient_contacts").select("patient_id").eq("contact_id", cid).execute()).data or []
            pids = {l["patient_id"] for l in links}
            print(f"{nome} ({phone}): contato {cid[-6:]} existe; {len(pids)} paciente(s) vinculado(s)")
            if not dry_run:
                for pid in pids:
                    other = (await client.from_("patient_contacts").select("contact_id").eq("patient_id", pid).execute()).data or []
                    distinct = {o["contact_id"] for o in other}
                    if distinct == {cid}:
                        await client.from_("patients").delete().eq("id", pid).execute()
                        print(f"   paciente {pid[-6:]} apagado")
                    else:
                        await client.from_("patient_contacts").delete().eq("patient_id", pid).eq("contact_id", cid).execute()
                        print(f"   paciente {pid[-6:]} mantido (contato compartilhado) — vínculo removido")
                await client.from_("patient_contacts").delete().eq("contact_id", cid).execute()
                await client.from_("contacts").update({"manual_hold": True}).eq("id", cid).execute()
                print(f"   contato {cid[-6:]} agora solto com manual_hold=true")
        else:
            print(f"{nome} ({phone}): sem contato — criar solto com manual_hold=true")
            if not dry_run:
                await client.from_("contacts").insert({"phone": phone, "name": nome, "manual_hold": True}).execute()
                print(f"   contato criado")
    print(f"\n{'DRY-RUN (nada gravado)' if dry_run else 'RECONCILIAÇÃO CONCLUÍDA'}")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--exec" not in sys.argv))
