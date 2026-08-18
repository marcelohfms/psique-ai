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
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.scheduling_stall import (
    fetch_abandoned, is_nudge_eligible, REPORT_EVENT, mark_handled,
)

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


async def main() -> None:
    from supabase import acreate_client
    from app.database import get_user_by_phone

    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    now = datetime.now(TZ)

    cases = await fetch_abandoned(client, now)

    # Mantém só quem NÃO será cutucado automaticamente (pausado OU frio).
    reportable: list[dict] = []
    for case in cases:
        phone = case["phone"]
        user = await get_user_by_phone(phone) or {}
        active = bool(user.get("active"))
        window = await _window_open_safe(client, phone, now) if active else False
        if is_nudge_eligible(active, window):
            continue  # o cron de nudge cuida deste
        case["name"] = user.get("name") or "(sem cadastro)"
        case["active"] = active
        reportable.append(case)

    if not reportable:
        print("Nenhum caso de agendamento abandonado para reportar — e-mail não enviado.")
        return

    today_str = now.strftime("%d/%m/%Y")
    lines = [
        f"Pacientes que começaram a agendar e não confirmaram — {today_str}",
        "=" * 60,
        "Estes pacientes viram horários com a Eva mas não fecharam a consulta, e",
        "NÃO estão sendo cutucados automaticamente (Eva pausada, ou já fora da",
        "janela de 24h do WhatsApp). Vale um contato manual da clínica.",
        "",
        f"Total: {len(reportable)}",
        "-" * 60,
        "",
    ]
    for case in reportable:
        lines.append(_fmt_case(case))
        lines.append("")

    body = "\n".join(lines)
    subject = f"Psique — Agendamentos não finalizados ({len(reportable)}) — {today_str}"

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
        await mark_handled(client, case["phone"], REPORT_EVENT,
                           {"offered_at": case["offered_at"].isoformat()})
    print(f"E-mail enviado: {len(reportable)} caso(s) reportado(s).")


async def _window_open_safe(client, phone: str, now: datetime) -> bool:
    """Wrapper fino sobre o _window_open do cron de pagamento (import tardio para
    manter o módulo leve e o teste fácil de mockar)."""
    from scripts.send_payment_reminders import _window_open
    return await _window_open(client, phone, now)


if __name__ == "__main__":
    asyncio.run(main())
