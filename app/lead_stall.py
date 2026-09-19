"""Situação do lead: classificação pura + labels do funil de lead.

Roda só na camada de cron (nenhum uso no webhook/grafo). A detecção lê tabelas
que já existem (messages, events, patients/contacts, appointments) e reusa
app/scheduling_stall.py para a parte de agendamento abandonado.

Labels (mutuamente exclusivas, no máximo uma por lead):
  - lead-novo             → começou, cadastro incompleto, ainda dentro do prazo
  - cadastro-abandonado   → deu o nome, cadastro incompleto, 4h+ de silêncio
  - agendamento-abandonado→ viu horários e não confirmou (via scheduling_stall)
"""
from datetime import datetime, timedelta

from app.scheduling_stall import parse_ts, fetch_abandoned

STALL_HOURS = 4
MAX_LEAD_AGE_DAYS = 7

LABEL_NEW = "lead-novo"
LABEL_CADASTRO = "cadastro-abandonado"
LABEL_AGENDAMENTO = "agendamento-abandonado"
LEAD_LABELS = (LABEL_NEW, LABEL_CADASTRO, LABEL_AGENDAMENTO)

# Eventos phone-keyed (tabela events) para memória entre rodadas do cron.
LABEL_SET_EVENT = "lead_label_set"       # metadata: {"label": <str|None>, "conversation_id": int}
NUDGE_EVENT = "lead_stall_nudge_sent"
REPORT_EVENT = "lead_stall_reported"


def classify_situation(
    *,
    active: bool,
    has_appointment: bool,
    offered_abandoned: bool,
    registration_complete: bool,
    has_name: bool,
    last_msg_at: datetime,
    now: datetime,
    stall_hours: int = STALL_HOURS,
) -> str | None:
    """Devolve a label da situação do lead, ou None quando não há label a aplicar.

    Ordem importa: quem tem consulta ou está pausado sai fora; agendamento
    abandonado precede o resto (implica cadastro completo); só então avaliamos o
    cadastro incompleto."""
    if not active:
        return None
    if has_appointment:
        return None
    if offered_abandoned:
        return LABEL_AGENDAMENTO
    if registration_complete:
        return None
    silent = (now - last_msg_at) >= timedelta(hours=stall_hours)
    if silent:
        return LABEL_CADASTRO if has_name else None
    return LABEL_NEW


def select_recent_phones(
    message_rows: list[dict],
    now: datetime,
    max_age_days: int = MAX_LEAD_AGE_DAYS,
) -> dict[str, datetime]:
    """Por telefone, o instante da última mensagem DO PACIENTE (role user) dentro
    da janela de max_age_days. Ignora mensagens do assistant e leads antigos —
    isso impede o primeiro deploy de acordar abandono velho."""
    cutoff = now - timedelta(days=max_age_days)
    latest: dict[str, datetime] = {}
    for row in message_rows:
        phone = row.get("phone")
        if not phone or row.get("role") != "user":
            continue
        ts = parse_ts(row["created_at"])
        if ts < cutoff:
            continue
        if phone not in latest or ts > latest[phone]:
            latest[phone] = ts
    return latest


def label_ops(situation: str | None) -> tuple[list[str], list[str]]:
    """(add, remove) para set_labels: adiciona a label da situação (se houver) e
    remove as outras labels de lead. Nunca toca eva-ativa/eva-inativa."""
    add = [situation] if situation else []
    remove = [lbl for lbl in LEAD_LABELS if lbl != situation]
    return add, remove


def needs_label_change(situation: str | None, last_set: str | None) -> bool:
    """Só chama o Chatwoot quando a situação mudou desde a última vez que setamos."""
    return situation != last_set


async def evaluate_leads(client, now: datetime) -> list[dict]:
    """Para cada telefone com mensagem do paciente nos últimos MAX_LEAD_AGE_DAYS,
    reúne os fatos do banco e classifica a situação. Devolve
    [{"phone", "situation", "user", "last_msg_at"}].

    Faz I/O (Supabase + shim get_user_by_phone/get_upcoming_appointments) — a
    lógica de decisão fica em classify_situation (pura, testada à parte)."""
    from app.database import (
        get_user_by_phone, get_upcoming_appointments, is_registration_complete,
    )

    cutoff_iso = (now - timedelta(days=MAX_LEAD_AGE_DAYS)).isoformat()
    rows = (
        await client.from_("messages")
        .select("phone, role, created_at")
        .gte("created_at", cutoff_iso)
        .execute()
    ).data or []
    latest = select_recent_phones(rows, now)

    abandoned = {c["phone"] for c in await fetch_abandoned(client, now)}

    records: list[dict] = []
    for phone, last_msg_at in latest.items():
        user = await get_user_by_phone(phone) or {}
        appts = await get_upcoming_appointments(phone)
        has_appointment = any(a.get("status") == "scheduled" for a in appts)
        situation = classify_situation(
            active=bool(user.get("active", True)),
            has_appointment=has_appointment,
            offered_abandoned=phone in abandoned,
            registration_complete=is_registration_complete(user),
            has_name=bool((user.get("name") or "").strip()),
            last_msg_at=last_msg_at,
            now=now,
        )
        records.append({
            "phone": phone,
            "situation": situation,
            "user": user,
            "last_msg_at": last_msg_at,
        })
    return records
