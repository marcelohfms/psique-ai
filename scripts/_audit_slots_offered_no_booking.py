"""Auditoria de "pediu data e não continuou" (carrinho abandonado de consulta).

Lista os telefones para quem a Eva OFERECEU horários (evento `slots_offered`,
emitido em get_available_slots quando horários reais são apresentados) e que,
passadas HOURS horas, NÃO confirmaram nenhuma consulta.

Correlação toda pela tabela phone-keyed `events`:
  - oferta      = evento `slots_offered`
  - conversão   = evento `appointment_booked` ou `appointment_rescheduled`
                  com created_at POSTERIOR à última oferta do telefone.

Cada caso abandonado é classificado pelo campo `active` do contato:
  🟢 eva-ativa (active=True)  → candidato ao re-contato automático da Eva (Etapa B)
  🟡 manual    (active=False) → Eva pausada (decisão da atendente / handoff);
                                relatório avisa a clínica: paciente aguardando retorno.

Read-only. Não envia nada, não altera nada.

Uso:
    uv run python scripts/_audit_slots_offered_no_booking.py [HORAS]
    (HORAS = janela mínima sem confirmar para contar como abandono; padrão 4)
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
DEFAULT_HOURS = 4

_CONVERSION_EVENTS = ("appointment_booked", "appointment_rescheduled")


def _parse(ts: str) -> datetime:
    """created_at do Supabase (ISO, UTC) → datetime aware."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def select_abandoned(
    latest_offer: dict[str, dict],
    booked_at: dict[str, list[datetime]],
    cutoff: datetime,
) -> list[dict]:
    """Lógica pura do abandono. Dado, por telefone, a ÚLTIMA oferta de horários e
    os instantes de confirmação (booking/reschedule), devolve os casos em que:
      - a última oferta é anterior a `cutoff` (janela de resposta já passou), e
      - não houve confirmação POSTERIOR a essa oferta.
    Uma confirmação anterior à oferta não conta (o paciente pediu datas de novo e
    parou). Ordenado por data da oferta."""
    abandoned: list[dict] = []
    for phone, ev in latest_offer.items():
        offered_at = _parse(ev["created_at"])
        if offered_at > cutoff:
            continue  # ainda dentro da janela de resposta
        if any(b > offered_at for b in booked_at.get(phone, [])):
            continue  # confirmou depois de ver os horários
        abandoned.append({"phone": phone, "offered_at": offered_at,
                          "metadata": ev.get("metadata") or {}})
    abandoned.sort(key=lambda c: c["offered_at"])
    return abandoned


async def main(hours: int) -> None:
    from app.database import get_supabase, get_user_by_phone

    client = await get_supabase()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    # ── Todas as ofertas de horário, mais recente por telefone ────────────────
    offers = (
        await client.from_("events")
        .select("phone, metadata, created_at")
        .eq("event_type", "slots_offered")
        .order("created_at")
        .execute()
    ).data or []

    latest_offer: dict[str, dict] = {}
    for ev in offers:
        phone = ev.get("phone")
        if not phone:
            continue
        latest_offer[phone] = ev  # ordenado asc → sobra o mais recente

    # ── Conversões (booking/reschedule) por telefone ──────────────────────────
    conversions = (
        await client.from_("events")
        .select("phone, created_at")
        .in_("event_type", list(_CONVERSION_EVENTS))
        .execute()
    ).data or []

    booked_at: dict[str, list[datetime]] = {}
    for ev in conversions:
        phone = ev.get("phone")
        if not phone:
            continue
        booked_at.setdefault(phone, []).append(_parse(ev["created_at"]))

    # ── Filtra abandonos: última oferta > HORAS atrás e sem confirmação após ──
    abandoned = select_abandoned(latest_offer, booked_at, cutoff)

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

    print(f"🟢 EVA-ATIVA — candidatos a re-contato automático (Etapa B): "
          f"{len(eva_ativa)}")
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
