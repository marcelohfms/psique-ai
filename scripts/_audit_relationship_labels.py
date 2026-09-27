"""Auditoria: parentescos em patient_contacts fora da lista do painel da atendente.

A Eva gravou por um tempo rótulos compostos ("avó/avô", "irmão/irmã", "tia/tio",
"mãe/pai") e respostas livres ("sou a cuidadora dele"). O painel só oferece uma
lista fechada (RELATIONSHIPS em dashboard/attendant_db.py, branch
painel-agendamento). Este script lista as linhas fora dessa lista.

Dry-run por padrão: só imprime. Com --apply, reescreve APENAS as variantes sem
ambiguidade (grafia sem acento, "tutora" → "tutor(a)", "responsavel" →
"responsável legal"...). Compostos como "avó/avô" não dá para decidir sozinho:
ficam listados para a atendente corrigir no painel, olhando a conversa.

Uso:
    uv run python scripts/_audit_relationship_labels.py
    uv run python scripts/_audit_relationship_labels.py --apply
"""
import argparse
import asyncio
from collections import Counter

from dotenv import load_dotenv

load_dotenv()

PANEL_RELATIONSHIPS = (
    "mãe", "pai", "avó", "avô", "tutor(a)", "responsável legal", "tio", "tia",
    "irmão", "irmã", "padrasto", "madrasta", "cônjuge", "acompanhante",
)

# Chave = forma normalizada (sem acento, minúscula) → rótulo do painel.
# Só entra aqui o que tem uma única leitura possível.
_UNAMBIGUOUS = {
    "mae": "mãe",
    "pai": "pai",
    "tutor": "tutor(a)",
    "tutora": "tutor(a)",
    "tutor(a)": "tutor(a)",
    "responsavel": "responsável legal",
    "responsavel legal": "responsável legal",
    "tio": "tio",
    "tia": "tia",
    "irmao": "irmão",
    "irma": "irmã",
    "padrasto": "padrasto",
    "madrasta": "madrasta",
    "conjuge": "cônjuge",
    "esposa": "cônjuge",
    "esposo": "cônjuge",
    "marido": "cônjuge",
    "acompanhante": "acompanhante",
}


def classify(relationship: str | None, is_self: bool) -> tuple[str, str | None]:
    """(situação, rótulo sugerido). Situação: ok | proprio | corrigivel | revisar."""
    from app.patients import _is_self_like, _norm_rel

    if relationship in PANEL_RELATIONSHIPS:
        return "ok", None
    if is_self and _is_self_like(relationship):
        return "proprio", None
    if relationship is None:
        return "revisar", None
    fixed = _UNAMBIGUOUS.get(_norm_rel(relationship))
    if fixed:
        return "corrigivel", fixed
    return "revisar", None


async def main(apply: bool) -> None:
    from app.patients import _is_guardian_relationship, _is_legal_guardian
    from app.supabase_client import get_supabase

    client = await get_supabase()
    rows: list[dict] = []
    page, size = 0, 1000
    while True:
        res = await (
            client.from_("patient_contacts")
            .select("id, patient_id, contact_id, role, is_self, relationship, "
                    "patients(name, birth_date), contacts(name, phone)")
            .range(page * size, page * size + size - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < size:
            break
        page += 1

    buckets: dict[str, list[dict]] = {"corrigivel": [], "revisar": []}
    labels = Counter()
    for r in rows:
        status, fixed = classify(r.get("relationship"), bool(r.get("is_self")))
        if status in buckets:
            r["_fixed"] = fixed
            buckets[status].append(r)
            labels[r.get("relationship")] += 1

    print(f"patient_contacts: {len(rows)} linhas")
    print(f"fora da lista do painel: {sum(labels.values())} linhas\n")
    print("Rótulos fora da lista (linhas | responsável? | legal?):")
    for rel, n in labels.most_common():
        print(f"  {rel!r:32} {n:4} | {_is_guardian_relationship(rel)!s:5} | {_is_legal_guardian(rel)}")

    def _line(r: dict) -> str:
        p = r.get("patients") or {}
        c = r.get("contacts") or {}
        return (f"  {p.get('name') or '?'} ({p.get('birth_date') or 's/ nasc.'}) ← "
                f"{c.get('name') or '?'} {c.get('phone') or ''} [{r['role']}] "
                f"rel={r.get('relationship')!r} is_self={r.get('is_self')}")

    print(f"\nCorrigíveis sem ambiguidade: {len(buckets['corrigivel'])}")
    for r in buckets["corrigivel"]:
        print(_line(r) + f" → {r['_fixed']!r}")
    print(f"\nPara revisar no painel: {len(buckets['revisar'])}")
    for r in buckets["revisar"]:
        print(_line(r))

    if not apply:
        print("\n(dry-run: nada foi alterado; use --apply para gravar os corrigíveis)")
        return
    for r in buckets["corrigivel"]:
        await (
            client.from_("patient_contacts")
            .update({"relationship": r["_fixed"]})
            .eq("id", r["id"])
            .execute()
        )
    print(f"\n{len(buckets['corrigivel'])} linhas atualizadas.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="grava os corrigíveis")
    asyncio.run(main(ap.parse_args().apply))
