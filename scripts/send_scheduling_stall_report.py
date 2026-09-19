"""Relatório diário à clínica de "pediu data e não continuou" que a Eva NÃO vai
cutucar sozinha. Roda 1x por dia (8h Recife) via GitHub Actions.

Inclui os casos abandonados (4h+ sem confirmar; ver app/scheduling_stall.py) que
NÃO são elegíveis a nudge automático:
  - Eva pausada / eva-inativa (active=False) — pode ser paciente que não quer
    atendimento por bot; a clínica decide o contato manual;
  - frios: fora da janela de 24h do WhatsApp (mensagem livre não seria entregue).

Casos ativos E dentro da janela ficam de fora — esses o send_scheduling_stall_nudges
cutuca automaticamente.

"Avisou, não repete": cada caso reportado grava o evento scheduling_stall_reported
e não entra no e-mail do dia seguinte.
"""
import asyncio
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.scheduling_stall import (
    fetch_abandoned, is_nudge_eligible, REPORT_EVENT as REPORT_EVENT_SCHED, mark_handled,
)
from app.lead_stall import evaluate_leads, LABEL_CADASTRO, REPORT_EVENT, NUDGE_EVENT
from app.database import get_events_by_type

TZ = ZoneInfo("America/Recife")


def _fmt_case(case: dict) -> str:
    md = case["metadata"]
    pedido = " / ".join(
        str(md[k]) for k in ("doctor", "preferred_day", "preferred_shift")
        if md.get(k)
    )
    quando = case["offered_at"].astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
    motivo = "Eva pausada (eva-inativa)" if not case["active"] else "fora da janela de 24h"
    line = f"• {case['name']}"
    line += f"\n  WhatsApp: {case['phone']}"
    line += f"\n  Viu horários em: {quando}"
    if pedido:
        line += f"\n  Havia pedido: {pedido}"
    line += f"\n  Motivo do contato manual: {motivo}"
    return line


async def fetch_cadastro_abandonado_reportable(client, now: datetime) -> list[dict]:
    """cadastro-abandonado que a Eva NÃO vai cutucar (pausado OU fora da janela de
    24h) e que ainda não foi reportado. Espelha a regra do relatório de
    agendamento."""
    records = await evaluate_leads(client, now)
    reportable: list[dict] = []
    for rec in records:
        if rec["situation"] != LABEL_CADASTRO:
            continue
        phone = rec["phone"]
        user = rec["user"]
        active = bool(user.get("active", True))
        window = await _window_open_safe(client, phone, now) if active else False
        if active and window:
            continue  # o cron de nudge cuida deste
        if await get_events_by_type(phone, REPORT_EVENT, limit=1):
            continue  # já reportado
        if await get_events_by_type(phone, NUDGE_EVENT, limit=1):
            continue  # a Eva já cutucou este lead — não pedir contato manual duplicado
        reportable.append({
            "phone": phone,
            "name": user.get("name") or "(sem cadastro)",
            "active": active,
            "last_msg_at": rec["last_msg_at"],
        })
    return reportable


def _fmt_cadastro_case(case: dict) -> str:
    quando = case["last_msg_at"].astimezone(TZ).strftime("%d/%m/%Y às %H:%M")
    motivo = "Eva pausada (eva-inativa)" if not case["active"] else "fora da janela de 24h"
    line = f"• {case['name']}"
    line += f"\n  WhatsApp: {case['phone']}"
    line += f"\n  Última mensagem em: {quando}"
    line += f"\n  Motivo do contato manual: {motivo}"
    return line


async def main() -> None:
    from supabase import acreate_client
    from app.database import get_user_by_phone

    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    now = datetime.now(TZ)

    # Seção 1: agendamento abandonado (comportamento existente).
    cases = await fetch_abandoned(client, now)
    reportable: list[dict] = []
    for case in cases:
        phone = case["phone"]
        user = await get_user_by_phone(phone) or {}
        active = bool(user.get("active"))
        window = await _window_open_safe(client, phone, now) if active else False
        if is_nudge_eligible(active, window):
            continue
        case["name"] = user.get("name") or "(sem cadastro)"
        case["active"] = active
        reportable.append(case)

    # Seção 2: cadastro abandonado frio (novo). Isolado: uma falha aqui não pode
    # derrubar o relatório de agendamento, que já funcionava sozinho.
    try:
        cadastro_cases = await fetch_cadastro_abandonado_reportable(client, now)
    except Exception as e:
        print(f"[cadastro] falha ao selecionar (mantendo só a seção de agendamento): "
              f"{type(e).__name__}: {e}")
        traceback.print_exc()
        cadastro_cases = []

    if not reportable and not cadastro_cases:
        print("Nenhum caso para reportar — e-mail não enviado.")
        return

    today_str = now.strftime("%d/%m/%Y")
    lines: list[str] = []

    if reportable:
        lines += [
            f"Pacientes que começaram a agendar e não confirmaram — {today_str}",
            "=" * 60,
            "Viram horários com a Eva mas não fecharam a consulta, e NÃO estão",
            "sendo cutucados automaticamente (Eva pausada, ou fora da janela de",
            "24h do WhatsApp). Vale um contato manual.",
            "",
            f"Total: {len(reportable)}",
            "-" * 60,
            "",
        ]
        for case in reportable:
            lines.append(_fmt_case(case))
            lines.append("")

    if cadastro_cases:
        lines += [
            f"Leads que começaram o cadastro e não terminaram — {today_str}",
            "=" * 60,
            "Informaram o nome mas não concluíram o cadastro, e NÃO estão sendo",
            "cutucados automaticamente (Eva pausada, ou fora da janela de 24h).",
            "Vale um contato manual.",
            "",
            f"Total: {len(cadastro_cases)}",
            "-" * 60,
            "",
        ]
        for case in cadastro_cases:
            lines.append(_fmt_cadastro_case(case))
            lines.append("")

    body = "\n".join(lines)
    total = len(reportable) + len(cadastro_cases)
    subject = f"Psique — Leads não finalizados ({total}) — {today_str}"

    print(body)
    print()

    missing = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "CLINIC_NOTIFY_EMAIL")
               if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"Variáveis de ambiente ausentes: {', '.join(missing)} — e-mail NÃO enviado.")

    from app.email_sender import send_clinic_notification_email
    await send_clinic_notification_email(subject, body)

    # "Avisou, não repete": marca cada caso só APÓS o e-mail sair.
    for case in reportable:
        await mark_handled(client, case["phone"], REPORT_EVENT_SCHED,
                           {"offered_at": case["offered_at"].isoformat()})
    for case in cadastro_cases:
        await mark_handled(client, case["phone"], REPORT_EVENT,
                           {"last_msg_at": case["last_msg_at"].isoformat()})
    print(f"E-mail enviado: {len(reportable)} agendamento(s) + {len(cadastro_cases)} cadastro(s).")


async def _window_open_safe(client, phone: str, now: datetime) -> bool:
    """Wrapper fino sobre o _window_open do cron de pagamento (import tardio para
    manter o módulo leve e o teste fácil de mockar)."""
    from scripts.send_payment_reminders import _window_open
    return await _window_open(client, phone, now)


if __name__ == "__main__":
    asyncio.run(main())
