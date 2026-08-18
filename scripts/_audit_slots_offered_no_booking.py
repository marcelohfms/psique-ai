"""Auditoria de "pediu data e não continuou" (carrinho abandonado de consulta).

Lista os telefones para quem a Eva OFERECEU horários (evento `slots_offered`,
emitido em get_available_slots quando horários reais são apresentados) e que,
passadas HOURS horas, NÃO confirmaram nenhuma consulta.

A lógica de abandono vive em app/scheduling_stall.py (compartilhada com os crons
send_scheduling_stall_nudges.py e send_scheduling_stall_report.py). Aqui é só o
relatório read-only: mostra o quadro COMPLETO (exclude_handled=False), inclusive
quem já recebeu nudge ou já foi reportado à clínica.

Cada caso abandonado é classificado pelo campo `active` do contato:
  🟢 eva-ativa (active=True)  → candidato ao re-contato automático da Eva (nudge)
  🟡 manual    (active=False) → Eva pausada (decisão da atendente / handoff);
                                relatório avisa a clínica: paciente aguardando retorno.

Read-only. Não envia nada, não altera nada.

Uso:
    uv run python scripts/_audit_slots_offered_no_booking.py [HORAS]
    (HORAS = janela mínima sem confirmar para contar como abandono; padrão 4)
"""
import asyncio
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

# Reexporta select_abandoned para compat. de imports/tests já existentes.
from app.scheduling_stall import select_abandoned, fetch_abandoned, DEFAULT_STALL_HOURS  # noqa: F401

TZ = ZoneInfo("America/Recife")
DEFAULT_HOURS = DEFAULT_STALL_HOURS


async def main(hours: int) -> None:
    from app.database import get_supabase, get_user_by_phone

    client = await get_supabase()
    now = datetime.now(timezone.utc)

    abandoned = await fetch_abandoned(client, now, hours=hours, exclude_handled=False)

    # ── Enriquece com nome + estado da Eva (active) e separa em buckets ───────
    eva_ativa: list[dict] = []
    manual: list[dict] = []
    for case in abandoned:
        user = await get_user_by_phone(case["phone"])
        case["name"] = (user or {}).get("name") or "(sem cadastro)"
        case["active"] = bool((user or {}).get("active"))
        (eva_ativa if case["active"] else manual).append(case)

    # ── Relatório ─────────────────────────────────────────────────────────────
    def _fmt(case: dict) -> str:
        md = case["metadata"]
        pedido = " / ".join(
            str(md[k]) for k in ("doctor", "preferred_day", "preferred_shift")
            if md.get(k)
        )
        quando = case["offered_at"].astimezone(TZ)
        return (f"  {case['phone']:<16} {case['name'][:28]:<28} "
                f"ofereceu {quando:%d/%m %H:%M}  [{pedido}]")

    print(f"\n=== Agendamento abandonado — ofereceu horário e não confirmou "
          f"há {hours}h+ (referência: {now.astimezone(TZ):%d/%m/%Y %H:%M}) ===\n")

    print(f"🟢 EVA-ATIVA — candidatos a re-contato automático: {len(eva_ativa)}")
    for case in eva_ativa:
        print(_fmt(case))

    print(f"\n🟡 MANUAL — Eva pausada, avisar clínica (paciente aguardando "
          f"retorno): {len(manual)}")
    for case in manual:
        print(_fmt(case))

    print(f"\nTotal abandonado: {len(abandoned)} "
          f"({len(eva_ativa)} eva-ativa + {len(manual)} manual)")


if __name__ == "__main__":
    hrs = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HOURS
    asyncio.run(main(hrs))
