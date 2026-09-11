"""Corrige o evento appointment_rescheduled da Maria Luiza (5581988788701).

Contexto (caso 03/09/2026): a consulta foi trocada de 02/10 11h para 25/09 11h
às 20h37, mas a taxa de reserva só foi registrada às 22h16 do mesmo dia. Ou seja,
a troca de data aconteceu ANTES do pagamento. Pela política corrigida, remarcação
antes do pagamento não consome o benefício de "1 remarcação com taxa transferida".

O evento antigo não tem o campo metadata.fee_paid (criado antes do fix). Sem ele, a
contagem trata como fee_paid nulo => conta. Este script marca fee_paid=False no
evento, alinhando ao que o código passa a gravar daqui pra frente.

Rode a PARTIR do repo principal (onde está o .env):
    uv run python .worktrees/fix-reschedule-fee-unpaid/scripts/_fix_maria_luiza_reschedule_fee_paid.py --apply
Sem --apply, só mostra o que faria (dry-run).
"""
import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

from app.supabase_client import get_supabase

PHONE = "5581988788701"
RESCHEDULE_TS = "2026-09-03T20:37"   # troca de data
FEE_PAID_TS = "2026-09-03T22:16"     # comprovante registrado (posterior)


async def main(apply: bool):
    client = await get_supabase()
    res = await (
        client.from_("events")
        .select("id, created_at, metadata")
        .eq("phone", PHONE)
        .eq("event_type", "appointment_rescheduled")
        .order("created_at")
        .execute()
    )
    rows = res.data or []
    if not rows:
        print("Nenhum evento appointment_rescheduled encontrado para", PHONE)
        return

    print(f"Encontrados {len(rows)} evento(s) appointment_rescheduled:")
    for r in rows:
        print(f"  id={r['id']} created_at={r['created_at']} metadata={r['metadata']}")

    # Só corrige eventos cuja remarcação ocorreu ANTES do pagamento da taxa e que
    # ainda não têm o campo fee_paid gravado.
    to_fix = [
        r for r in rows
        if (r.get("created_at") or "") < FEE_PAID_TS
        and "fee_paid" not in (r.get("metadata") or {})
    ]
    if not to_fix:
        print("\nNada a corrigir (nenhum evento pré-pagamento sem fee_paid).")
        return

    print(f"\n{len(to_fix)} evento(s) serão marcados com fee_paid=False:")
    for r in to_fix:
        new_meta = dict(r.get("metadata") or {})
        new_meta["fee_paid"] = False
        print(f"  id={r['id']}: {r.get('metadata')} -> {new_meta}")
        if apply:
            await client.from_("events").update({"metadata": new_meta}).eq("id", r["id"]).execute()

    if apply:
        print("\n✅ Aplicado.")
        chk = await (
            client.from_("events").select("id, metadata")
            .eq("phone", PHONE).eq("event_type", "appointment_rescheduled")
            .order("created_at").execute()
        )
        print("Estado final:")
        for r in chk.data or []:
            print(f"  id={r['id']} metadata={r['metadata']}")
    else:
        print("\n(dry-run) Rode com --apply para gravar.")


asyncio.run(main("--apply" in sys.argv))
