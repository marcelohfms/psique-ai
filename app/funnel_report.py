"""Relatório de funil de leads (lógica pura, sem I/O).

Etapas do funil, por telefone, dentro de um cohort de chegada:
  Interessados → Qualificados → Agendados (agendou E pagou/isento).
Quem não converteu vira Pendente (ativo nos últimos STALL_DAYS dias) ou
Perdido (frio). "Agendado" só conta com a taxa resolvida (evento de pagamento
ou isenção), por decisão de produto.

Consumido por scripts/send_funnel_report.py (lê events+messages e chama isto).
"""
from datetime import datetime, timedelta

FUNNEL_WINDOW_DAYS = 30   # cohort: chegou nos últimos 30 dias
STALL_DAYS = 7            # separa pendente (quente) de perdido (frio)

# Tipos de evento (tabela events) que marcam cada etapa.
ARRIVAL_EVENT = "conversation_started"
QUALIFIED_EVENT = "slots_offered"
BOOKED_EVENT = "appointment_booked"
PAID_EVENTS = ("payment_receipt_registered", "booking_fee_registered", "booking_fee_waived")


def etapa_alcancada(phone: str, *, qualified: set, booked: set, paid: set) -> str:
    """A etapa mais avançada que o telefone alcançou."""
    if phone in paid:
        return "Agendado"
    if phone in booked:
        return "Agendou, falta pagar"
    if phone in qualified:
        return "Qualificado"
    return "Interessado"


def compute_funnel(*, cohort: set, qualified: set, booked: set, paid: set,
                   last_activity: dict, now: datetime, stall_days: int = STALL_DAYS) -> dict:
    """Conta o funil e separa os não-convertidos em pendentes/perdidos.

    - Convertido = tem pagamento/isenção resolvido (está em `paid`).
    - Qualificado (contagem) = alcançou ao menos a etapa de ver horários
      (qualified ∪ booked ∪ paid), tudo dentro do cohort.
    - Pendente = não-convertido com última atividade nos últimos stall_days.
    - Perdido = não-convertido frio (sem atividade recente)."""
    converted = cohort & paid
    reached_qualified = cohort & (qualified | booked | paid)
    non_converted = cohort - converted

    threshold = now - timedelta(days=stall_days)
    pendentes: list[dict] = []
    perdidos = 0
    for phone in non_converted:
        la = last_activity.get(phone)
        if la is not None and la >= threshold:
            pendentes.append({
                "phone": phone,
                "etapa": etapa_alcancada(phone, qualified=qualified, booked=booked, paid=paid),
            })
        else:
            perdidos += 1

    interessados = len(cohort)
    agendados = len(converted)
    conversao_pct = round(100 * agendados / interessados) if interessados else 0
    pendentes.sort(key=lambda p: p["phone"])

    return {
        "interessados": interessados,
        "qualificados": len(reached_qualified),
        "agendados": agendados,
        "conversao_pct": conversao_pct,
        "pendentes": pendentes,
        "perdidos": perdidos,
    }


def format_report(result: dict, now: datetime, tz) -> tuple[str, str]:
    """Monta (assunto, corpo) do e-mail. Os pendentes podem trazer 'name'."""
    hoje = now.astimezone(tz).strftime("%d/%m/%Y")
    q_drop = result["interessados"] - result["qualificados"]
    a_drop = result["qualificados"] - result["agendados"]

    lines = [
        f"Funil de leads — {hoje}",
        "=" * 50,
        f"Interessados   {result['interessados']}",
        f"Qualificados   {result['qualificados']}   (-{q_drop} no cadastro)",
        f"Agendados      {result['agendados']}   (-{a_drop} entre qualificar e pagar)",
        "",
        f"Conversão: {result['agendados']} de {result['interessados']} ({result['conversao_pct']}%)",
        "",
        f"Pendentes (quentes, ativos até {STALL_DAYS} dias): {len(result['pendentes'])}",
        f"Perdidos (frios): {result['perdidos']}",
        "",
        "-" * 50,
        "Pendentes para contato:",
    ]
    if result["pendentes"]:
        for p in result["pendentes"]:
            nome = (p.get("name") or "").strip()
            quem = f"{nome} ({p['phone']})" if nome else p["phone"]
            lines.append(f"• {quem} — {p['etapa']}")
    else:
        lines.append("(nenhum)")

    body = "\n".join(lines)
    subject = f"Psique — Funil de leads ({result['agendados']}/{result['interessados']}) — {hoje}"
    return subject, body
