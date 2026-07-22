"""
Send WhatsApp return reminders (retorno periódico) via Meta Cloud API templates.
Runs once a day via GitHub Actions.

Reminders are driven by `return_reminders` (populated by the /retornos
dashboard, where the doctor sets a return_interval per patient after seeing
them). next_return_date is compared to today by CALENDAR MONTH, not exact
days:
  - month before next_return_date's month -> retorno_um_mes_antes
  - same month as next_return_date        -> retorno_no_mes
  - after next_return_date's month        -> retorno_atrasado (envio único)

`15_dias` rows never fire (all 3 flags pre-marked at classification time —
see dashboard/return_reminders.py::save_classification). `1_mes` rows never
fire retorno_um_mes_antes for the same reason (that flag is also pre-marked).

Sends are throttled — batches of 10, 60s pause between batches — to reduce
risk of the WhatsApp number being flagged for spam. Only the flag for a
successfully-sent reminder is marked, so anything left over from today's run
is retried automatically on tomorrow's run.

Requires in Supabase: tabela `return_reminders` (ver
supabase/migrations/20260714_create_return_reminders.sql).
"""
import asyncio
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.patients import get_contacts_for_patient

TZ = ZoneInfo("America/Recife")
BATCH_SIZE = 10
BATCH_PAUSE_SECONDS = 60

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}
DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def _month_key(d: date) -> int:
    return d.year * 12 + d.month


def pending_template(today: date, row: dict) -> tuple[str, str] | None:
    """Retorna (template_name, coluna_de_flag) a disparar hoje para esta linha, ou None."""
    if row.get("return_interval") == "15_dias":
        return None

    next_return_date = date.fromisoformat(row["next_return_date"])
    current_key = _month_key(today)
    target_key = _month_key(next_return_date)

    if row.get("month_before_sent_at") is None and current_key == target_key - 1:
        return ("retorno_um_mes_antes", "month_before_sent_at")
    if row.get("month_of_sent_at") is None and current_key == target_key:
        return ("retorno_no_mes", "month_of_sent_at")
    if row.get("overdue_sent_at") is None and current_key > target_key:
        return ("retorno_atrasado", "overdue_sent_at")
    return None
