"""Relatório diário de falhas de leitura de comprovante (lógica pura, sem I/O).

Fontes (tabela events, gravadas por app/receipt_reconciliation.py e pelo fluxo de
comprovante ilegível):
  receipt_drop_alerted       — comprovante que NÃO foi lido na chegada (caiu) e a rede avisou.
  receipt_drop_replayed      — o mesmo anexo foi relido e reinjetado com sucesso depois.
  payment_receipt_unreadable — comprovante chegou e foi lido, mas estava ilegível.

Um "caiu" cujo anexo não tem um "resgatado" correspondente é o que ainda precisa de
atenção humana (a rede avisou mas não conseguiu reinjetar sozinha). Consumido por
scripts/send_daily_receipt_failure_report.py, que lê os events e chama isto.
"""
import html as _html
from datetime import datetime
from zoneinfo import ZoneInfo

from app.phone import _phone_variants
from app.receipt_reconciliation import RECEIPT_DROP_EVENT, REPLAY_EVENT

UNREADABLE_EVENT = "payment_receipt_unreadable"
REPORT_EVENT_TYPES = [RECEIPT_DROP_EVENT, REPLAY_EVENT, UNREADABLE_EVENT]

# Números que NÃO são pacientes da Psiquê e poluem o relatório. O Chatwoot é
# compartilhado com outro projeto (ver memória chatwoot-instancia-compartilhada-dois-projetos):
# a reconciliação acaba flagrando anexos dele. Guardado como telefone cru; a comparação
# normaliza pelas variantes (com/sem o 9). Acrescente aqui se aparecerem outros.
EXCLUDED_PHONES_RAW = ("558191812399",)  # Clínica Affectus

_LABELS = {
    RECEIPT_DROP_EVENT: "caiu",
    REPLAY_EVENT: "resgatado",
    UNREADABLE_EVENT: "ilegível",
}


def _canonical(phone: str) -> str:
    """Forma canônica do telefone para comparar e agrupar (com/sem o 9 colapsam)."""
    variants = _phone_variants(phone or "")
    return variants[0] if variants else "".join(c for c in (phone or "") if c.isdigit())


def _excluded_set(excluded) -> set[str]:
    raw = EXCLUDED_PHONES_RAW if excluded is None else excluded
    return {_canonical(p) for p in raw}


def _pending_attachment_ids(events) -> set:
    """attachment_ids que caíram (alerted) e NÃO têm um resgate (replayed) correspondente."""
    alerted, replayed = set(), set()
    for e in events:
        att = (e.get("metadata") or {}).get("attachment_id")
        if att is None:
            continue
        if e["event_type"] == RECEIPT_DROP_EVENT:
            alerted.add(att)
        elif e["event_type"] == REPLAY_EVENT:
            replayed.add(att)
    return alerted - replayed


def build_report(events, *, now: datetime, tz: ZoneInfo, name_of, excluded=None) -> dict:
    """Monta a estrutura do relatório a partir de uma lista de events (já na janela).

    events: dicts com event_type, phone, created_at (ISO UTC), metadata.
    name_of: callable phone -> nome do paciente (resolvido com I/O pelo chamador).
    excluded: iterável de telefones a ignorar (default: EXCLUDED_PHONES_RAW).
    """
    excl = _excluded_set(excluded)
    kept, n_excluidos = [], 0
    for e in events:
        if _canonical(e.get("phone", "")) in excl:
            n_excluidos += 1
            continue
        kept.append(e)

    pending_ids = _pending_attachment_ids(kept)
    totais = {"caiu": 0, "resgatado": 0, "ilegivel": 0, "pendentes": len(pending_ids)}

    por_dia: dict[str, list[dict]] = {}
    for e in kept:
        et = e["event_type"]
        if et == RECEIPT_DROP_EVENT:
            totais["caiu"] += 1
        elif et == REPLAY_EVENT:
            totais["resgatado"] += 1
        elif et == UNREADABLE_EVENT:
            totais["ilegivel"] += 1

        dt_local = datetime.fromisoformat(e["created_at"]).astimezone(tz)
        dia = dt_local.strftime("%d/%m/%Y")
        att = (e.get("metadata") or {}).get("attachment_id")
        por_dia.setdefault(dia, []).append({
            "hora": dt_local.strftime("%H:%M"),
            "_ordem": dt_local,
            "categoria": _LABELS.get(et, et),
            "nome": name_of(e.get("phone", "")),
            "phone": e.get("phone", ""),
            "attachment_id": att,
            "pendente": att in pending_ids,
        })

    dias_ordenados = []
    for dia in sorted(por_dia, key=lambda d: datetime.strptime(d, "%d/%m/%Y")):
        itens = sorted(por_dia[dia], key=lambda i: i["_ordem"])
        for i in itens:
            i.pop("_ordem", None)
        dias_ordenados.append({"dia": dia, "itens": itens})

    return {"por_dia": dias_ordenados, "totais": totais, "n_excluidos": n_excluidos}


def _period_label(now: datetime, tz: ZoneInfo, por_dia) -> str:
    if por_dia:
        if len(por_dia) == 1:
            return por_dia[0]["dia"]
        return f"{por_dia[0]['dia']} a {por_dia[-1]['dia']}"
    return now.astimezone(tz).strftime("%d/%m/%Y")


def format_report(result: dict, *, now: datetime, tz: ZoneInfo) -> tuple[str, str]:
    """Assunto e corpo em texto puro."""
    t = result["totais"]
    periodo = _period_label(now, tz, result["por_dia"])
    pend = t["pendentes"]
    alerta = f" — {pend} ainda sem resgate" if pend else ""
    subject = f"Falhas de leitura de comprovante — {periodo}{alerta}"

    linhas = [f"Falhas de leitura de comprovante — {periodo}", ""]
    if not result["por_dia"]:
        linhas.append("Nenhuma falha registrada no período. 🎉")
    else:
        for dia in result["por_dia"]:
            linhas.append(f"── {dia['dia']} — {len(dia['itens'])} ocorrência(s)")
            for i in dia["itens"]:
                flag = "  ⚠️ PRECISA DE ATENÇÃO" if i["pendente"] else ""
                linhas.append(
                    f"   {i['hora']}  {i['categoria']:10s}  {i['nome']}  "
                    f"({i['phone']}){flag}"
                )
            linhas.append("")
        linhas.append("── Resumo:")
        linhas.append(f"   Caíram (não lidos na chegada): {t['caiu']}")
        linhas.append(f"   Resgatados automaticamente:    {t['resgatado']}")
        linhas.append(f"   Ilegíveis:                     {t['ilegivel']}")
        linhas.append(f"   Ainda sem resgate (atenção):   {t['pendentes']}")
    if result["n_excluidos"]:
        linhas.append("")
        linhas.append(f"({result['n_excluidos']} anexo(s) de outro projeto no Chatwoot foram ignorados.)")
    return subject, "\n".join(linhas)


def build_html_report(result: dict, *, now: datetime, tz: ZoneInfo) -> str:
    t = result["totais"]
    periodo = _period_label(now, tz, result["por_dia"])
    parts = [f"<h2>Falhas de leitura de comprovante — {_html.escape(periodo)}</h2>"]
    if not result["por_dia"]:
        parts.append("<p>Nenhuma falha registrada no período. 🎉</p>")
    else:
        for dia in result["por_dia"]:
            parts.append(f"<h3>{_html.escape(dia['dia'])} — {len(dia['itens'])} ocorrência(s)</h3>")
            parts.append("<table cellpadding='4' style='border-collapse:collapse'>")
            parts.append(
                "<tr><th align='left'>Hora</th><th align='left'>Situação</th>"
                "<th align='left'>Paciente</th><th align='left'>Número</th><th></th></tr>"
            )
            for i in dia["itens"]:
                flag = "⚠️ precisa de atenção" if i["pendente"] else ""
                bg = " style='background:#fff3cd'" if i["pendente"] else ""
                parts.append(
                    f"<tr{bg}><td>{_html.escape(i['hora'])}</td>"
                    f"<td>{_html.escape(i['categoria'])}</td>"
                    f"<td>{_html.escape(str(i['nome']))}</td>"
                    f"<td>{_html.escape(i['phone'])}</td>"
                    f"<td>{flag}</td></tr>"
                )
            parts.append("</table>")
        parts.append(
            f"<p><b>Resumo:</b> caíram {t['caiu']}, resgatados {t['resgatado']}, "
            f"ilegíveis {t['ilegivel']}, ainda sem resgate <b>{t['pendentes']}</b>.</p>"
        )
    if result["n_excluidos"]:
        parts.append(
            f"<p><i>{result['n_excluidos']} anexo(s) de outro projeto no Chatwoot "
            "foram ignorados.</i></p>"
        )
    return "\n".join(parts)
