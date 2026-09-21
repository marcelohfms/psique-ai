"""Relatório semanal de funil de leads. Roda via GitHub Actions.

Read-only sobre `events` e `messages`. Envia SÓ para a dona (FUNNEL_REPORT_EMAIL),
nunca para a clínica. Ver app/funnel_report.py para as etapas e regras.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.funnel_report import (
    compute_funnel, format_report,
    FUNNEL_WINDOW_DAYS, ARRIVAL_EVENT, QUALIFIED_EVENT, BOOKED_EVENT, PAID_EVENTS,
)
from app.scheduling_stall import parse_ts
from app.database import get_user_by_phone
from app.email_sender import send_report_email

TZ = ZoneInfo("America/Recife")

_FUNNEL_EVENT_TYPES = [ARRIVAL_EVENT, QUALIFIED_EVENT, BOOKED_EVENT, *PAID_EVENTS]


async def run(client, now: datetime) -> None:
    """Lê os dados, calcula o funil e envia. `client` e `now` injetados para teste."""
    to_email = os.environ.get("FUNNEL_REPORT_EMAIL")
    if not to_email:
        raise EnvironmentError(
            "FUNNEL_REPORT_EMAIL não setado — relatório de funil NÃO enviado. "
            "Setar com o e-mail da dona (não usar o da clínica)."
        )

    cutoff = (now - timedelta(days=FUNNEL_WINDOW_DAYS)).astimezone(timezone.utc).isoformat()

    # Volume de 30 dias numa clínica pequena cabe numa página do Supabase; se um dia
    # crescer muito, paginar com .range().
    rows = (
        await client.from_("events")
        .select("phone, event_type, created_at")
        .in_("event_type", _FUNNEL_EVENT_TYPES)
        .gte("created_at", cutoff)
        .execute()
    ).data or []

    cohort, qualified, booked, paid = set(), set(), set(), set()
    for r in rows:
        phone = r.get("phone")
        if not phone:
            continue
        et = r.get("event_type")
        if et == ARRIVAL_EVENT:
            cohort.add(phone)
        elif et == QUALIFIED_EVENT:
            qualified.add(phone)
        elif et == BOOKED_EVENT:
            booked.add(phone)
        elif et in PAID_EVENTS:
            paid.add(phone)

    mrows = (
        await client.from_("messages")
        .select("phone, role, created_at")
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(50000)
        .execute()
    ).data or []
    last_activity: dict[str, datetime] = {}
    # Atividade = mensagem do próprio paciente (role user); resposta automática da
    # Eva não conta como sinal de vida do lead.
    for m in mrows:
        phone = m.get("phone")
        if not phone or m.get("role") != "user":
            continue
        ts = parse_ts(m["created_at"])
        if phone not in last_activity or ts > last_activity[phone]:
            last_activity[phone] = ts

    result = compute_funnel(
        cohort=cohort, qualified=qualified, booked=booked, paid=paid,
        last_activity=last_activity, now=now,
    )

    # Enriquece os pendentes com o nome (o e-mail vai só para a dona). Uma falha
    # pontual numa busca não pode derrubar o relatório todo — cai para nome vazio.
    for p in result["pendentes"]:
        try:
            user = await get_user_by_phone(p["phone"]) or {}
        except Exception as e:
            print(f"  [funnel] falha ao buscar nome de {p['phone']}: {type(e).__name__}: {e}")
            user = {}
        p["name"] = user.get("patient_name") or user.get("name") or ""

    subject, body = format_report(result, now, TZ)
    print(
        f"Funil (últimos {FUNNEL_WINDOW_DAYS} dias): "
        f"interessados={result['interessados']} qualificados={result['qualificados']} "
        f"agendados={result['agendados']} pendentes={len(result['pendentes'])} "
        f"perdidos={result['perdidos']}"
    )
    await send_report_email(subject, body, to_email)
    print(f"Relatório de funil enviado para {to_email}.")


async def main() -> None:
    from supabase import acreate_client
    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    await run(client, now)


if __name__ == "__main__":
    asyncio.run(main())
