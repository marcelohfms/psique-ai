"""Agendamento feito pela atendente no painel (endpoint /admin/panel/appointments).

Reaproveita as regras da Eva: grade (grid_violation), textos (booking_texts),
destinatários pela regra da idade (consultation_reminder_contacts). A mensagem
enviada entra em `messages` e no checkpoint da conversa, para a Eva saber do que
se trata quando o paciente responder.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.database import DOCTOR_IDS, get_supabase
from app.google_calendar import grid_violation
from app.utils import display_name

_logger = logging.getLogger(__name__)
TZ = ZoneInfo("America/Recife")

# Mesmos textos em dashboard/templates/atendente.html (SPLIT_PART1/SPLIT_PART2).
SPLIT_PART1 = "1ª consulta · parte 1 de 2"
SPLIT_PART2 = "1ª consulta · parte 2 de 2"

_GRID_REASONS = {
    "dia_bloqueado": "dia bloqueado na agenda",
    "fora_da_excecao": "fora do horário de atendimento",
    "dia_sem_atendimento": "o médico não atende neste dia da semana",
    "fora_da_grade": "fora do horário de atendimento",
    "estoura_expediente": "passa do fim do expediente",
}


def _to_local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


async def _calendar_busy(doctor: str, start: datetime, end: datetime) -> list[dict]:
    """Eventos da Eva no Calendar do médico entre start e end. Erro = lista vazia
    (a checagem no banco continua valendo)."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import _credentials, _get_busy
    from googleapiclient.discovery import build
    try:
        calendar_id = await _get_doctor_calendar_id(doctor)
        service = build("calendar", "v3", credentials=_credentials())
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _get_busy, service, calendar_id, start, end)
    except Exception:
        _logger.exception("panel check_slot: falha ao ler o Calendar doctor=%s", doctor)
        return []


async def check_slot(doctor: str, start: datetime, minutes: int, patient_id: str) -> list[str]:
    """Motivos pelos quais o horário é encaixe. Lista vazia = horário livre na grade.

    Consultas do próprio paciente não contam como conflito (a 2ª parte da 1ª
    consulta infantil pode encostar na 1ª)."""
    reasons: list[str] = []
    code = grid_violation(doctor, start, minutes)
    if code:
        reasons.append(_GRID_REASONS[code])

    end = start + timedelta(minutes=minutes)
    client = await get_supabase()
    res = await (
        client.from_("appointments")
        .select("patient_id, start_time, patients(name)")
        .eq("doctor_id", DOCTOR_IDS[doctor])
        .eq("status", "scheduled")
        .lt("start_time", end.isoformat())
        .gt("end_time", start.isoformat())
        .execute()
    )
    clash_starts = set()
    for row in res.data or []:
        if row.get("patient_id") == patient_id:
            continue
        other = display_name((row.get("patients") or {}).get("name") or "") or "outro paciente"
        hhmm = _to_local(row["start_time"]).strftime("%H:%M")
        clash_starts.add(hhmm)
        reasons.append(f"bate com a consulta de {other} às {hhmm}")

    # Calendar pega também o que foi marcado à mão pela clínica (evento "Consulta ...").
    for ev in await _calendar_busy(doctor, start, end):
        hhmm = _to_local(ev["start"]).strftime("%H:%M")
        if hhmm not in clash_starts:
            reasons.append(f"agenda do médico ocupada às {hhmm}")
            clash_starts.add(hhmm)
    return reasons
