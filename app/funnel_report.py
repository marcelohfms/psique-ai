"""Relatório de funil de leads (lógica pura, sem I/O).

Etapas do funil, por telefone, dentro de um cohort de chegada:
  Interessados → Qualificados → Agendados (agendou E pagou/isento).
Quem não converteu vira Pendente (ativo nos últimos STALL_DAYS dias) ou
Perdido (frio). "Agendado" só conta com a taxa resolvida (evento de pagamento
ou isenção), por decisão de produto.

Consumido por scripts/send_funnel_report.py (lê events+messages e chama isto).
"""
import html as _html
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
    # Convertido = pagamento/isenção resolvido. Não exigimos appointment_booked
    # junto: pagar implica ter agendado, e o booking pode ser anterior à janela ou
    # vir pelo dashboard — o pagamento é o sinal firme de conversão.
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
        f"Funil de leads (últimos {FUNNEL_WINDOW_DAYS} dias) — {hoje}",
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


# Cores da "pill" por etapa: (fundo, texto).
_ETAPA_PILL = {
    "Agendado": ("#dcfce7", "#166534"),
    "Agendou, falta pagar": ("#fef3c7", "#92400e"),
    "Qualificado": ("#e0f2fe", "#075985"),
    "Interessado": ("#f1f5f9", "#475569"),
}


def _bar_width_pct(part: int, whole: int) -> int:
    """Largura da barra em %, com mínimo visual de 6% para valor > 0 nunca sumir."""
    if not whole or part <= 0:
        return 0
    return max(6, round(100 * part / whole))


def _pendente_row(p: dict) -> str:
    nome = _html.escape((p.get("name") or "").strip())
    phone = _html.escape(p.get("phone") or "")
    quem = nome if nome else phone
    etapa = p.get("etapa") or ""
    bg, fg = _ETAPA_PILL.get(etapa, ("#f1f5f9", "#475569"))
    etapa_html = (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:10px;font-size:12px;">{_html.escape(etapa)}</span>'
    )
    return (
        '<tr>'
        f'<td style="padding:8px 10px;color:#0f172a;border-bottom:1px solid #f1f5f9;">{quem}</td>'
        f'<td style="padding:8px 10px;color:#334155;border-bottom:1px solid #f1f5f9;">{phone}</td>'
        f'<td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;">{etapa_html}</td>'
        '</tr>'
    )


def build_html_report(result: dict, now: datetime, tz) -> str:
    """Monta o corpo HTML do relatório (estilos inline, layout em tabela, sem JS
    nem CSS externo — seguro para clientes de e-mail). Visual aprovado no mockup."""
    hoje = now.astimezone(tz).strftime("%d/%m/%Y")
    inter = result["interessados"]
    qual = result["qualificados"]
    agen = result["agendados"]
    conv = result["conversao_pct"]
    q_drop = inter - qual
    a_drop = qual - agen
    qual_w = _bar_width_pct(qual, inter)
    agen_w = _bar_width_pct(agen, inter)

    q_drop_html = (
        f'<div style="font-size:11px;color:#ef4444;margin:0 0 8px 130px;">'
        f'↓ {q_drop} desistiram no cadastro</div>' if q_drop > 0 else ""
    )
    a_drop_html = (
        f'<div style="font-size:11px;color:#ef4444;margin:0 0 4px 130px;">'
        f'↓ {a_drop} viram horário e não pagaram</div>' if a_drop > 0 else ""
    )

    if result["pendentes"]:
        rows = "".join(_pendente_row(p) for p in result["pendentes"])
    else:
        rows = ('<tr><td colspan="3" style="padding:8px 10px;color:#94a3b8;'
                'font-size:13px;">(nenhum)</td></tr>')

    return f"""\
<div style="background:#eef1f4;padding:24px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <tr><td style="background:#0f766e;padding:20px 24px;">
      <div style="color:#ffffff;font-size:18px;font-weight:700;">Funil de leads — Clínica Psique</div>
      <div style="color:#b8e0db;font-size:13px;margin-top:2px;">Semana de {hoje} · últimos {FUNNEL_WINDOW_DAYS} dias</div>
    </td></tr>

    <tr><td style="padding:24px 24px 8px 24px;text-align:center;">
      <div style="font-size:40px;font-weight:800;color:#0f766e;line-height:1;">{conv}%</div>
      <div style="font-size:13px;color:#64748b;margin-top:4px;">conversão · {agen} de {inter} leads viraram consulta paga</div>
    </td></tr>

    <tr><td style="padding:16px 24px 8px 24px;">
      <div style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;">O funil</div>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;"><tr>
        <td style="width:130px;font-size:13px;color:#334155;">Interessados</td>
        <td><div style="background:#3b82f6;height:26px;width:100%;border-radius:5px;color:#fff;font-size:13px;font-weight:700;line-height:26px;padding-left:10px;">{inter}</div></td>
      </tr></table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:2px;"><tr>
        <td style="width:130px;font-size:13px;color:#334155;">Qualificados</td>
        <td><div style="background:#0ea5e9;height:26px;width:{qual_w}%;border-radius:5px;color:#fff;font-size:13px;font-weight:700;line-height:26px;padding-left:10px;">{qual}</div></td>
      </tr></table>
      {q_drop_html}

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:2px;"><tr>
        <td style="width:130px;font-size:13px;color:#334155;">Agendados (pagos)</td>
        <td><div style="background:#0f766e;height:26px;width:{agen_w}%;border-radius:5px;color:#fff;font-size:13px;font-weight:700;line-height:26px;padding-left:10px;">{agen}</div></td>
      </tr></table>
      {a_drop_html}
    </td></tr>

    <tr><td style="padding:16px 24px 8px 24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="width:50%;padding-right:6px;">
          <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:12px;text-align:center;">
            <div style="font-size:24px;font-weight:800;color:#ea580c;">{len(result['pendentes'])}</div>
            <div style="font-size:12px;color:#9a3412;">Pendentes (quentes)</div>
          </div>
        </td>
        <td style="width:50%;padding-left:6px;">
          <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;text-align:center;">
            <div style="font-size:24px;font-weight:800;color:#64748b;">{result['perdidos']}</div>
            <div style="font-size:12px;color:#64748b;">Perdidos (frios)</div>
          </div>
        </td>
      </tr></table>
    </td></tr>

    <tr><td style="padding:16px 24px 24px 24px;">
      <div style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Pendentes para contato</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;">
        <tr style="background:#f1f5f9;">
          <td style="padding:8px 10px;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0;">Paciente</td>
          <td style="padding:8px 10px;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0;">WhatsApp</td>
          <td style="padding:8px 10px;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0;">Etapa</td>
        </tr>
        {rows}
      </table>
      <div style="font-size:11px;color:#94a3b8;margin-top:10px;">Relatório automático · enviado só para você</div>
    </td></tr>
  </table>
</div>"""
