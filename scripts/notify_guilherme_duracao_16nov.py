"""Aviso pontual de duração para UMA consulta de acompanhamento (16/11/2026).

TEMPORÁRIO (criar e remover). Essa consulta foi agendada por engano com 2h; a
duração já foi corrigida para 1h no banco e no Google Calendar. Este cron avisa
o contato responsável que houve uma confusão e que o acompanhamento dura só 1
hora — mas SÓ depois que a presença for confirmada (appointments.confirmed_at).

O telefone e os nomes NÃO ficam no código: são resolvidos no banco em tempo de
execução a partir do identificador interno da consulta.

Garantias:
- Idempotente: envia uma única vez (grava o evento-marcador e confere antes).
- Só age dentro da janela de datas (14 a 16/11/2026, Recife). Fora dela, é um
  noop instantâneo — não toca no banco. Por isso pode ser deixado rodando.
- Só envia se a consulta ainda estiver 'scheduled' e com 1h de fato.

REMOVER depois de 16/11/2026: apague este arquivo e o Job
"notify_guilherme_duracao" em app/scheduler.py. Roda via agendador interno
(python -m scripts.notify_guilherme_duracao_16nov).
"""
import asyncio
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

# Alvo fixo: esta consulta (id interno) deste paciente (id interno). UUIDs
# opacos — sem telefone, sem nome.
APPT_DB_ID = "4b0cef13-ff7b-473f-85dc-47f4609b4957"
EXPECTED_PATIENT_ID = "11218df5-85f3-40ea-855b-226bae9a6609"
MARKER_EVENT = "aviso_duracao_acompanhamento_1h_16nov"

# Janela de datas em que faz sentido rodar (Recife). Fora dela, noop sem banco.
WINDOW_START = date(2026, 11, 14)
WINDOW_END = date(2026, 11, 16)

# {contato} e {paciente} são preenchidos com o 1º nome vindo do banco.
CONTENT_TEMPLATE = (
    "Oi, {contato}! 😊\n\n"
    "Que bom que confirmou a consulta do {paciente} para segunda, dia 16/11, "
    "às 14h, presencial com o Dr. Júlio.\n\n"
    "Preciso só corrigir uma informação. Houve uma confusão aqui no nosso "
    "agendamento sobre a duração. Como o {paciente} já é paciente, essa consulta "
    "é de acompanhamento, e o acompanhamento dura 1 hora.\n\n"
    "Então a consulta vai das 14h às 15h. O resto continua igual, mesmo dia, "
    "mesmo horário de início e com o Dr. Júlio.\n\n"
    "Qualquer dúvida, é só me chamar!"
)


def in_window(now: datetime) -> bool:
    d = now.astimezone(TZ).date()
    return WINDOW_START <= d <= WINDOW_END


def duration_minutes(start_iso: str, end_iso: str) -> int:
    s = datetime.fromisoformat(start_iso)
    e = datetime.fromisoformat(end_iso)
    return int((e - s).total_seconds() // 60)


def first_name(full: str | None) -> str:
    return (full or "").strip().split(" ")[0] if (full or "").strip() else ""


def build_message(contato: str, paciente: str) -> str:
    return CONTENT_TEMPLATE.format(contato=contato, paciente=paciente)


def should_notify(appt: dict | None, already_sent: bool) -> tuple[bool, str]:
    """Decide se deve enviar o aviso. Pura (sem I/O) para ser testável."""
    if appt is None:
        return False, "consulta não encontrada"
    if appt.get("patient_id") != EXPECTED_PATIENT_ID:
        return False, "patient_id diferente do esperado"
    if already_sent:
        return False, "já avisado"
    if appt.get("status") != "scheduled":
        return False, f"status={appt.get('status')!r} (não está marcada)"
    dur = duration_minutes(appt["start_time"], appt["end_time"])
    if dur != 60:
        return False, f"duração={dur}min (ainda não é 1h)"
    if not appt.get("confirmed_at"):
        return False, "presença ainda não confirmada"
    return True, "ok"


async def _resolve_phone_and_names(client, appt: dict) -> tuple[str | None, str, str]:
    """Telefone do contato responsável e 1º nome do contato e do paciente."""
    contact_phone = None
    contact_name = None
    if appt.get("contact_id"):
        c = await (
            client.from_("contacts").select("phone, name")
            .eq("id", appt["contact_id"]).limit(1).execute()
        )
        if c.data:
            contact_phone = c.data[0].get("phone")
            contact_name = c.data[0].get("name")
    p = await (
        client.from_("patients").select("name")
        .eq("id", appt["patient_id"]).limit(1).execute()
    )
    patient_name = p.data[0].get("name") if p.data else None
    return contact_phone, first_name(contact_name), first_name(patient_name)


async def _send_notice(phone: str, content: str) -> int:
    from app.chatwoot import find_or_create_conversation, send_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    await send_message(conv_id, content)
    return conv_id


async def process(client, now: datetime | None = None) -> int:
    """Retorna 1 se enviou, 0 caso contrário. Idempotente e guardado."""
    now = now or datetime.now(TZ)
    if not in_window(now):
        print("notify_guilherme_duracao: fora da janela de datas; nada a fazer.")
        return 0

    row = await (
        client.from_("appointments")
        .select("id, patient_id, contact_id, status, start_time, end_time, confirmed_at")
        .eq("id", APPT_DB_ID)
        .limit(1)
        .execute()
    )
    appt = (row.data or [None])[0]

    phone = None
    if appt is not None:
        phone, contato, paciente = await _resolve_phone_and_names(client, appt)

    already_sent = False
    if phone:
        ev = await (
            client.from_("events").select("id")
            .eq("event_type", MARKER_EVENT).eq("phone", phone)
            .limit(1).execute()
        )
        already_sent = bool(ev.data)

    ok, reason = should_notify(appt, already_sent)
    if ok and not phone:
        ok, reason = False, "sem telefone do contato"
    print(f"notify_guilherme_duracao: {reason}")
    if not ok:
        return 0

    from app.database import log_event, save_message
    content = build_message(contato, paciente)
    conv_id = await _send_notice(phone, content)
    await save_message(phone, "assistant", content)
    await log_event(MARKER_EVENT, phone, {
        "appointment_db_id": APPT_DB_ID,
        "patient_id": EXPECTED_PATIENT_ID,
    })
    print(f"notify_guilherme_duracao: aviso enviado (conversa {conv_id}).")
    return 1


async def main():
    from supabase import acreate_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)
    await process(client)


if __name__ == "__main__":
    asyncio.run(main())
