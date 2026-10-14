"""Classificação de retorno periódico do paciente (dashboard /retornos).

Autocontido: não importa app/ (a imagem Docker do dashboard não contém app/).
"""
import asyncio
import logging
import os
from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from payments import DOCTOR_KEY

_TZ = ZoneInfo("America/Recife")
_log = logging.getLogger(__name__)

RETURN_INTERVALS = ("15_dias", "1_mes", "2_meses", "3_meses", "4_meses", "6_meses")

ALTA = "alta"  # sentinela em return_interval: paciente recebeu alta, sem próximo retorno

RETURN_INTERVAL_LABELS = {
    "15_dias": "15 dias",
    "1_mes": "1 mês",
    "2_meses": "2 meses",
    "3_meses": "3 meses",
    "4_meses": "4 meses",
    "6_meses": "6 meses",
}

DOCTOR_ID_BY_KEY = {key: doctor_id for doctor_id, key in DOCTOR_KEY.items()}


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _local_date_time(start_time: str | None) -> tuple[str, str]:
    """Converte start_time (ISO, vem em UTC do Postgres) pro fuso de Recife.

    Retorna (data 'aaaa-mm-dd', hora 'HH:MM') — ambos "" se start_time for
    None/vazio. Sem isso, tanto a exibição quanto a data enviada ao salvar a
    classificação ficam erradas perto da virada do dia (uma consulta às 22h
    em Recife já é dia seguinte em UTC) — mesma classe de bug já corrigida
    no FakeQuery de teste (ver dashboard/tests/conftest.py).
    """
    if not start_time:
        return "", ""
    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00")).astimezone(_TZ)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def _with_local_date_time(appointments: list[dict]) -> list[dict]:
    for appt in appointments:
        appt["local_date"], appt["local_time"] = _local_date_time(appt.get("start_time"))
    return appointments


def compute_next_return_date(appointment_date: date, return_interval: str) -> date:
    """Calcula next_return_date a partir da data da consulta e do intervalo escolhido pelo médico."""
    if return_interval == "15_dias":
        return appointment_date + timedelta(days=15)
    if return_interval == "1_mes":
        return _add_months(appointment_date, 1)
    if return_interval == "2_meses":
        return _add_months(appointment_date, 2)
    if return_interval == "3_meses":
        return _add_months(appointment_date, 3)
    if return_interval == "4_meses":
        return _add_months(appointment_date, 4)
    if return_interval == "6_meses":
        return _add_months(appointment_date, 6)
    raise ValueError(f"return_interval inválido: {return_interval!r}")


async def get_today_appointments(client, doctor_id: str, today: date | None = None) -> list[dict]:
    """Pacientes com consulta hoje para o médico — sempre visível, para classificar na hora.

    Exclui `status = 'canceled'`: um reagendamento no mesmo dia deixa o slot
    antigo cancelado e cria um novo — sem esse filtro, os dois aparecem e o
    paciente fica duplicado na lista (caso Jonas Leonardo, 2026-07-22).
    """
    if today is None:
        today = datetime.now(_TZ).date()
    start = datetime(today.year, today.month, today.day, tzinfo=_TZ)
    end = start + timedelta(days=1)
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .neq("status", "canceled")
        .gte("start_time", start.isoformat())
        .lt("start_time", end.isoformat())
        .order("start_time")
        .execute()
    )
    return _with_local_date_time(result.data or [])


def drop_today_already_pending(hoje: list[dict], pendentes: list[dict]) -> list[dict]:
    """Remove de "Hoje" as consultas que já estão em "Pendentes de classificação".

    Uma consulta de hoje que já terminou entra na fila de pendentes; sem esse
    filtro o mesmo paciente apareceria nas duas seções no mesmo dia. A clínica
    pediu que, ao cair em pendentes, o paciente saia da lista de hoje.

    Casa por `appointment_id` (não por paciente) para não derrubar uma segunda
    consulta do mesmo paciente no mesmo dia que ainda não terminou.
    """
    pending_ids = {p.get("appointment_id") for p in pendentes}
    return [a for a in hoje if a.get("appointment_id") not in pending_ids]


def _has_ended(end_time: str | None, now: datetime) -> bool:
    """True se a consulta já terminou (end_time < now). Sem end_time -> False."""
    if not end_time:
        return False
    return datetime.fromisoformat(end_time.replace("Z", "+00:00")) < now


async def get_pending_classification(
    client, doctor_id: str, now: datetime | None = None
) -> list[dict]:
    """Pacientes cuja consulta mais recente já realizada ainda não foi classificada.

    Ordenados da mais antiga para a mais nova (fila que vai esvaziando).

    Inclui consultas ainda `scheduled` que JÁ terminaram (end_time < now), não
    só as `completed`: complete_appointments.py só marca `completed` 24h após o
    fim da consulta, então uma consulta de ontem ficava num vão — não era mais
    "de hoje" (get_today_appointments) nem ainda `completed` — e sumia da lista
    de retornos até o cron rodar (caso Bruna Araújo Parente, consulta 10/08
    invisível no dia 11). Isso só antecipa o que já apareceria: o cron marca
    todas as consultas passadas como `completed` de qualquer forma.

    Consultas futuras ainda `scheduled` (end_time >= now) continuam de fora —
    só entram depois de acontecerem, e assim não ofuscam uma consulta passada
    ainda pendente. Canceladas nunca entram.
    """
    if now is None:
        now = datetime.now(_TZ)
    appts_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, end_time, status, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .in_("status", ["completed", "scheduled"])
        .order("start_time")
        .execute()
    )
    latest_by_patient: dict[str, dict] = {}
    for appt in appts_result.data or []:
        if appt.get("status") == "no_show":
            continue  # falta nunca entra na fila de classificação
        patient_id = appt.get("patient_id")
        if not patient_id:
            continue
        # 'scheduled' só conta se a consulta já terminou; 'completed' sempre.
        if appt.get("status") == "scheduled" and not _has_ended(appt.get("end_time"), now):
            continue
        current = latest_by_patient.get(patient_id)
        if current is None or appt["start_time"] > current["start_time"]:
            latest_by_patient[patient_id] = appt

    if not latest_by_patient:
        return []

    rr_result = await (
        client.from_("return_reminders")
        .select("patient_id, last_classified_appointment_id")
        .in_("patient_id", list(latest_by_patient.keys()))
        .execute()
    )
    classified = {
        row["patient_id"]: row.get("last_classified_appointment_id")
        for row in (rr_result.data or [])
    }

    pending = [
        appt for appt in latest_by_patient.values()
        if classified.get(appt["patient_id"]) != appt["appointment_id"]
    ]
    pending.sort(key=lambda a: a["start_time"])
    return _with_local_date_time(pending)


async def save_classification(
    client,
    patient_id: str,
    doctor_id: str,
    appointment_id: str,
    appointment_date: date,
    return_interval: str,
) -> dict:
    """Grava/atualiza a classificação de retorno de um paciente (1 linha por paciente).

    Zera as flags de envio (novo ciclo). Casos especiais: `15_dias` já marca
    as 3 flags como enviadas (intervalo curto demais para os lembretes
    mensais fazerem sentido); `1_mes` já marca `month_before_sent_at` (o
    mês-alvo é sempre o mês seguinte à própria classificação, então "um mês
    antes" sairia colado na consulta que acabou de acontecer).
    """
    if return_interval not in RETURN_INTERVALS:
        raise ValueError(f"return_interval inválido: {return_interval!r}")

    next_return_date = compute_next_return_date(appointment_date, return_interval)
    now = datetime.now(_TZ).isoformat()

    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "return_interval": return_interval,
        "next_return_date": next_return_date.isoformat(),
        "last_classified_appointment_id": appointment_id,
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
        "updated_at": now,
    }
    if return_interval == "15_dias":
        payload["month_before_sent_at"] = now
        payload["month_of_sent_at"] = now
        payload["overdue_sent_at"] = now
    elif return_interval == "1_mes":
        payload["month_before_sent_at"] = now

    existing = await (
        client.from_("return_reminders").select("id").eq("patient_id", patient_id).execute()
    )
    if existing.data:
        result = await (
            client.from_("return_reminders").update(payload).eq("patient_id", patient_id).execute()
        )
    else:
        result = await client.from_("return_reminders").insert(payload).execute()
    return (result.data or [payload])[0]


# Marca visual de falta no Google Calendar: cor "Tomate" (vermelho) e "❌" no
# título. Espelha a convenção da confirmação (verde + "✅") em app/google_calendar.
# O "✅" some quando vira falta: quem não compareceu não pode ficar marcado como
# presença confirmada. (O dashboard não importa app/ — a imagem Docker não o tem —
# então a lógica do Calendar vive aqui, autocontida.)
_NO_SHOW_COLOR_ID = "11"
_NO_SHOW_PREFIX = "❌ "
_CONFIRMED_PREFIX = "✅ "


def _no_show_summary(summary: str) -> str:
    """Novo título do evento: tira o "✅" de presença e põe "❌" de falta.

    Idempotente — não empilha "❌" se já estiver lá — e preserva o resto do
    título, inclusive edições manuais da clínica."""
    summary = summary or ""
    if summary.startswith(_CONFIRMED_PREFIX.strip()):
        summary = summary[len(_CONFIRMED_PREFIX.strip()):].lstrip()
    if not summary.startswith(_NO_SHOW_PREFIX.strip()):
        summary = f"{_NO_SHOW_PREFIX}{summary}".strip()
    return summary


def _patch_event_no_show_sync(calendar_id: str, event_id: str) -> None:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=creds)
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    patch = {"summary": _no_show_summary(event.get("summary") or ""), "colorId": _NO_SHOW_COLOR_ID}
    service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch).execute()


async def _mark_calendar_no_show(client, doctor_id: str | None, event_id: str) -> None:
    """Pinta o evento de vermelho com "❌". Best-effort: se o Calendar falhar
    (evento apagado à mão, agenda indisponível), a falta já está gravada no banco
    — fonte da verdade — e nada é desfeito."""
    if not doctor_id or not event_id:
        return
    try:
        res = await (
            client.from_("doctors")
            .select("agenda_id")
            .eq("doctor_id", doctor_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        calendar_id = rows[0].get("agenda_id") if rows else None
        if not calendar_id:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _patch_event_no_show_sync, calendar_id, event_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("CALENDAR_NO_SHOW_FAILED appt=%s error=%s", event_id, exc)


async def mark_no_show(client, appointment_id: str) -> None:
    """Marca a consulta como falta (`status='no_show'`).

    Registro durável e de primeira classe: distingue quem faltou de quem
    compareceu. Como `get_pending_classification` e `compute_pendencias` só
    olham `completed`/`scheduled`, isso tira a consulta da fila do médico e
    das pendências da atendente. A taxa já paga fica retida (default passivo);
    override é manual, pela atendente, no fluxo de reembolso existente.

    Além do banco, pinta o evento no Google Calendar de vermelho com "❌" para
    a clínica enxergar a falta na agenda. O Calendar é best-effort: o banco é a
    fonte da verdade e não é desfeito se a pintura falhar.
    """
    res = await (
        client.from_("appointments")
        .select("doctor_id")
        .eq("appointment_id", appointment_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    doctor_id = rows[0].get("doctor_id") if rows else None

    await (
        client.from_("appointments")
        .update({"status": "no_show", "updated_at": datetime.now(_TZ).isoformat()})
        .eq("appointment_id", appointment_id)
        .execute()
    )

    await _mark_calendar_no_show(client, doctor_id, appointment_id)


async def save_discharge(
    client,
    patient_id: str,
    doctor_id: str,
    appointment_id: str,
) -> dict:
    """Registra ALTA do paciente: para os lembretes de retorno sem agendar um novo.

    Grava a mesma linha 1-por-paciente de `save_classification`, mas com o
    sentinela `return_interval="alta"` e `next_return_date=None`. O
    `last_classified_appointment_id` é o que tira a consulta da fila do
    /retornos. Não valida contra RETURN_INTERVALS de propósito — "alta" é um
    desfecho terminal, não um intervalo.
    """
    now = datetime.now(_TZ).isoformat()
    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "return_interval": ALTA,
        "next_return_date": None,
        "last_classified_appointment_id": appointment_id,
        "month_before_sent_at": now,
        "month_of_sent_at": now,
        "overdue_sent_at": now,
        "updated_at": now,
    }
    existing = await (
        client.from_("return_reminders").select("id").eq("patient_id", patient_id).execute()
    )
    if existing.data:
        result = await (
            client.from_("return_reminders").update(payload).eq("patient_id", patient_id).execute()
        )
    else:
        result = await client.from_("return_reminders").insert(payload).execute()
    return (result.data or [payload])[0]
