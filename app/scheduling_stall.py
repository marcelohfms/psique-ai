"""Lógica compartilhada do rastreio de "pediu data e não continuou"
(agendamento abandonado).

Fonte única: eventos phone-keyed na tabela `events`:
  - oferta      → slots_offered              (emitido em get_available_slots)
  - conversão   → appointment_booked | appointment_rescheduled  (posterior à oferta)
  - já tratado  → scheduling_nudge_sent | scheduling_stall_reported

Um caso abandonado termina em EXATAMENTE uma ação, marcada por evento e nunca
repetida:
  - nudge automático da Eva  → só quem está ativo (não eva-inativa/pausado) E dentro
    da janela de 24h do WhatsApp;
  - e-mail à clínica (contato manual) → todos os demais (pausados, que podem não
    querer atendimento por bot, e os frios fora da janela de 24h).

Consumido por:
  - scripts/_audit_slots_offered_no_booking.py  (relatório read-only)
  - scripts/send_scheduling_stall_nudges.py     (cron: nudge ao paciente)
  - scripts/send_scheduling_stall_report.py     (cron: e-mail à clínica)
"""
from datetime import datetime, timedelta, timezone

DEFAULT_STALL_HOURS = 4

OFFER_EVENT = "slots_offered"
CONVERSION_EVENTS = ("appointment_booked", "appointment_rescheduled")
NUDGE_EVENT = "scheduling_nudge_sent"
REPORT_EVENT = "scheduling_stall_reported"
HANDLED_EVENTS = (NUDGE_EVENT, REPORT_EVENT)


def parse_ts(ts: str) -> datetime:
    """created_at do Supabase (ISO) → datetime aware (assume UTC se sem tz)."""
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def select_abandoned(
    latest_offer: dict[str, dict],
    booked_at: dict[str, list[datetime]],
    cutoff: datetime,
    handled: set[str] | None = None,
) -> list[dict]:
    """Lógica pura do abandono. Dada, por telefone, a ÚLTIMA oferta de horários e
    os instantes de confirmação (booking/reschedule), devolve os casos em que:
      - o telefone ainda não foi tratado (não está em `handled`),
      - a última oferta é anterior a `cutoff` (janela de resposta já passou), e
      - não houve confirmação POSTERIOR a essa oferta.
    Uma confirmação anterior à oferta não conta (o paciente pediu datas de novo e
    parou). Ordenado por data da oferta."""
    handled = handled or set()
    abandoned: list[dict] = []
    for phone, ev in latest_offer.items():
        if phone in handled:
            continue
        offered_at = parse_ts(ev["created_at"])
        if offered_at > cutoff:
            continue  # ainda dentro da janela de resposta
        if any(b > offered_at for b in booked_at.get(phone, [])):
            continue  # confirmou depois de ver os horários
        abandoned.append({"phone": phone, "offered_at": offered_at,
                          "metadata": ev.get("metadata") or {}})
    abandoned.sort(key=lambda c: c["offered_at"])
    return abandoned


async def fetch_abandoned(
    client,
    now: datetime,
    hours: int = DEFAULT_STALL_HOURS,
    exclude_handled: bool = True,
) -> list[dict]:
    """Lê os eventos no Supabase e devolve os casos abandonados (via select_abandoned).

    exclude_handled=True remove quem já recebeu nudge ou já foi reportado à clínica
    (os crons); os relatórios read-only passam False para ver o quadro completo."""
    cutoff = now - timedelta(hours=hours)

    offers = (
        await client.from_("events")
        .select("phone, metadata, created_at")
        .eq("event_type", OFFER_EVENT)
        .order("created_at")
        .execute()
    ).data or []
    latest_offer: dict[str, dict] = {}
    for ev in offers:
        if ev.get("phone"):
            latest_offer[ev["phone"]] = ev  # asc → sobra o mais recente

    conversions = (
        await client.from_("events")
        .select("phone, created_at")
        .in_("event_type", list(CONVERSION_EVENTS))
        .execute()
    ).data or []
    booked_at: dict[str, list[datetime]] = {}
    for ev in conversions:
        if ev.get("phone"):
            booked_at.setdefault(ev["phone"], []).append(parse_ts(ev["created_at"]))

    handled: set[str] = set()
    if exclude_handled:
        rows = (
            await client.from_("events")
            .select("phone")
            .in_("event_type", list(HANDLED_EVENTS))
            .execute()
        ).data or []
        handled = {ev["phone"] for ev in rows if ev.get("phone")}

    return select_abandoned(latest_offer, booked_at, cutoff, handled)


def is_nudge_eligible(active: bool, window_open: bool) -> bool:
    """Só recebe nudge automático da Eva quem está ATIVO (não eva-inativa/pausado —
    pode ser paciente que não quer atendimento por bot) E dentro da janela de 24h
    do WhatsApp (para a mensagem livre ser entregue, sem depender de template).
    Todos os demais vão para o e-mail da clínica."""
    return bool(active) and bool(window_open)


async def mark_handled(client, phone: str, event_type: str,
                       metadata: dict | None = None) -> None:
    """Registra o evento terminal (nudge enviado ou reportado à clínica) para que o
    caso não seja tratado de novo. Reusa log_event (fire-and-forget, strip de phone)."""
    from app.database import log_event
    await log_event(event_type, phone, metadata or {})
