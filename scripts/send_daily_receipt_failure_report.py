"""Relatório DIÁRIO de falhas de leitura de comprovante. Roda pelo agendador interno
(app/scheduler.py) às 8h de Recife e cobre o dia anterior inteiro.

Read-only sobre `events`. Envia SÓ para a dona (RECEIPT_FAILURE_REPORT_EMAIL), nunca
para a clínica. As regras e o formato estão em app/receipt_failure_report.py.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.receipt_failure_report import (
    build_report, format_report, build_html_report, REPORT_EVENT_TYPES,
)
from app.database import get_user_by_phone
from app.email_sender import send_report_email

TZ = ZoneInfo("America/Recife")


async def run(client, now: datetime) -> None:
    """Lê os events do dia anterior, monta o relatório e envia. `client`/`now` injetados
    para teste."""
    to_email = os.environ.get("RECEIPT_FAILURE_REPORT_EMAIL")
    if not to_email:
        raise EnvironmentError(
            "RECEIPT_FAILURE_REPORT_EMAIL não setado — relatório de falhas de comprovante "
            "NÃO enviado. Setar com o e-mail da dona (não usar o da clínica)."
        )

    # Dia anterior inteiro, em Recife, convertido para UTC na consulta.
    today_local = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = today_local - timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).isoformat()
    end_utc = today_local.astimezone(timezone.utc).isoformat()

    rows = (
        await client.from_("events")
        .select("phone, event_type, created_at, metadata")
        .in_("event_type", REPORT_EVENT_TYPES)
        .gte("created_at", start_utc).lt("created_at", end_utc)
        .order("created_at", desc=False)
        .execute()
    ).data or []

    # Pré-resolve os nomes (I/O) para o build_report, que é puro. Falha pontual numa
    # busca não derruba o relatório — cai para "(sem nome)".
    names: dict[str, str] = {}
    for phone in {r.get("phone") for r in rows if r.get("phone")}:
        try:
            user = await get_user_by_phone(phone) or {}
        except Exception as e:
            print(f"  [falhas-comprovante] nome de {phone}: {type(e).__name__}: {e}")
            user = {}
        names[phone] = user.get("patient_name") or user.get("name") or "(sem nome)"

    result = build_report(rows, now=now, tz=TZ, name_of=lambda p: names.get(p, "(sem nome)"))
    subject, body = format_report(result, now=now, tz=TZ)
    html = build_html_report(result, now=now, tz=TZ)

    t = result["totais"]
    print(
        f"Falhas de leitura (dia anterior): caiu={t['caiu']} resgatado={t['resgatado']} "
        f"ilegivel={t['ilegivel']} pendentes={t['pendentes']} "
        f"(excluidos={result['n_excluidos']})"
    )
    await send_report_email(subject, body, to_email, html_body=html)
    print(f"Relatório de falhas de comprovante enviado para {to_email}.")


async def main() -> None:
    from supabase import acreate_client
    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    await run(client, now)


if __name__ == "__main__":
    asyncio.run(main())
