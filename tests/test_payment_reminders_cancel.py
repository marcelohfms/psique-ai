import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

import scripts.send_payment_reminders as spr

TZ = ZoneInfo("America/Recife")


def _appt(**kw):
    base = {
        "appointment_id": "evt-abc",
        "start_time": "2026-08-03T17:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "patient_id": "p-joao",
        "patients": {"name": "João Pedro"},
    }
    base.update(kw)
    return base


_CHAINED = ("update", "eq", "select", "single", "ilike", "gte", "order", "limit",
            "in_", "contains", "not_", "is_")


def _table(rows):
    t = MagicMock()
    for m in _CHAINED:
        getattr(t, m).return_value = t
    t.execute = AsyncMock(return_value=MagicMock(data=rows))
    return t


def _client(receipt_rows=None, event_rows=None):
    """Supabase double que despacha por tabela. `receipt_rows` alimenta a busca do
    comprovante em `messages`; `event_rows` alimenta a checagem de "já avisei a
    clínica sobre esta consulta" em `events` (default: nunca avisada).

    Precisa ser por tabela: com um único double as duas consultas devolviam as
    mesmas linhas, e um teste de comprovante encontrado passaria por acidente na
    dedup de e-mail."""
    default = _table(receipt_rows or [])
    tables = {
        "messages": default,
        "events": _table(event_rows or []),
    }
    client = MagicMock()
    client.from_.side_effect = lambda name: tables.get(name, default)
    return client, default


@pytest.mark.asyncio
async def test_cancel_logs_appointment_canceled_event_per_notified_contact():
    """Toda vez que o cron cancela uma consulta por falta de pagamento, precisa
    registrar um evento appointment_canceled em `events` — igual a todo outro
    caminho de cancelamento (cancel_appointment em app/graph/tools.py). Sem isso, a
    tabela `events` não mostra nenhum rastro do cancelamento, e investigar um caso
    exige cruzar Chatwoot e Google Calendar na mão em vez de ler uma tabela (caso
    João Pedro Lins Da Costa Gomes, 5581992349207, 2026-07-30)."""
    client, table = _client()
    now = datetime(2026, 7, 29, 12, 26, tzinfo=TZ)
    appt = _appt()

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581992349207", "name": "Ednara"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_log_event.assert_awaited_once_with("appointment_canceled", "5581992349207", {
        "appointment_id": "evt-abc",
        "reason": "unpaid_booking_fee",
        "start_time": "2026-08-03T17:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
    })


@pytest.mark.asyncio
async def test_cancel_does_not_log_event_when_no_contact_notified():
    """Se nenhum contato financeiro foi notificado com sucesso, o cancelamento é
    adiado (comportamento existente) — e portanto nenhum evento deve ser
    registrado, já que a consulta continua scheduled."""
    client, table = _client()
    now = datetime(2026, 7, 29, 12, 26, tzinfo=TZ)
    appt = _appt()

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581992349207", "name": "Ednara"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp",
               new_callable=AsyncMock, side_effect=Exception("WhatsApp down")), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event:
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_log_event.assert_not_awaited()
    table.update.assert_not_called()


# ── Guarda: comprovante na conversa bloqueia o cancelamento ──────────────────
# Caso Bernardo Lima Beltrão Teixeira (5581987415206, 31/07/2026): a mãe enviou o
# comprovante da taxa, Eva respondeu "recebemos!" mas não chamou register_payment
# (a conversa estava presa no collect_info, um nó sem ferramentas), então
# booking_fee_paid_at ficou NULL e este cron cancelou a consulta 4h depois.

_RECEIPT_ROW = [{
    "phone": "5581987415206",
    "content": "[imagem]: COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00 [drive_link:https://drive.google.com/file/d/abc/view]",
    "created_at": "2026-07-31T17:27:29+00:00",
}]


@pytest.mark.asyncio
async def test_cancel_blocked_when_receipt_found_in_conversation():
    """Com um comprovante na conversa, o cron NÃO cancela, NÃO mexe no Calendar e
    NÃO manda a mensagem de cancelamento — avisa a clínica para conferir na mão."""
    client, table = _client(receipt_rows=_RECEIPT_ROW)
    now = datetime(2026, 7, 31, 20, 15, tzinfo=TZ)
    appt = _appt(created_at="2026-07-31T14:30:44+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Raphaelle"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_wpp.assert_not_awaited()
    mock_cal.assert_not_awaited()
    table.update.assert_not_called()
    mock_email.assert_awaited_once()
    assert "bloqueado" in mock_email.await_args.args[0].lower()
    mock_log_event.assert_awaited_once()
    assert mock_log_event.await_args.args[0] == "payment_cancel_blocked_receipt_found"


@pytest.mark.asyncio
async def test_cancel_blocked_when_receipt_lookup_fails():
    """Fail-closed: se a consulta ao histórico falhar, não dá para provar que NÃO
    há comprovante — então o cancelamento é bloqueado, não executado."""
    client, table = _client()
    table.execute = AsyncMock(side_effect=Exception("supabase down"))
    now = datetime(2026, 7, 31, 20, 15, tzinfo=TZ)
    appt = _appt(created_at="2026-07-31T14:30:44+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Raphaelle"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_wpp.assert_not_awaited()
    mock_cal.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
async def test_receipt_lookup_ignores_messages_older_than_the_booking():
    """O comprovante precisa ser posterior ao agendamento: um comprovante de uma
    consulta anterior não pode segurar o cancelamento da consulta nova."""
    client, table = _client()
    since = "2026-07-31T14:30:44+00:00"

    await spr.find_receipt_in_conversation(client, ["5581987415206@s.whatsapp.net"], since)

    table.gte.assert_called_once_with("created_at", since)
    table.eq.assert_any_call("role", "user")


@pytest.mark.asyncio
async def test_receipt_lookup_covers_both_phone_variants():
    """`contacts.phone` guarda 5581999999999 e `messages.phone` frequentemente
    guarda 558199999999 (o 9 extra dos celulares brasileiros). Na auditoria de
    31/07/2026, 46 de 1000 contatos tinham o comprovante gravado SÓ sob a outra
    variante — para esses o guarda não achava nada e o cancelamento seguia."""
    client, table = _client()

    await spr.find_receipt_in_conversation(client, ["5581987415206@s.whatsapp.net"], "2026-07-31T14:30:44+00:00")

    variants = table.in_.call_args[0][1]
    assert "5581987415206" in variants
    assert "558187415206" in variants


@pytest.mark.asyncio
async def test_receipt_lookup_skips_typed_claims_without_an_image():
    """O ilike no banco é só um pré-filtro barato: ele casa com quem apenas digitou
    "segue o comprovante de pagamento". Bloquear nesses casos seguraria a vaga para
    sempre sem nada para a clínica conferir."""
    client, _ = _client(receipt_rows=[
        {"phone": "5581987415206", "content": "Segue o comprovante de pagamento",
         "created_at": "2026-07-31T18:00:00+00:00"},
    ])

    found = await spr.find_receipt_in_conversation(
        client, ["5581987415206@s.whatsapp.net"], "2026-07-31T14:30:44+00:00")

    assert found is None


@pytest.mark.asyncio
async def test_receipt_lookup_picks_the_real_receipt_past_a_typed_claim():
    """A busca traz várias linhas em ordem decrescente; a mais recente pode ser o
    texto solto e o comprovante de verdade vir logo atrás."""
    client, _ = _client(receipt_rows=[
        {"phone": "5581987415206", "content": "já mandei o comprovante de pagamento",
         "created_at": "2026-07-31T18:05:00+00:00"},
        {"phone": "558187415206",
         "content": "[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00.",
         "created_at": "2026-07-31T18:00:00+00:00"},
    ])

    found = await spr.find_receipt_in_conversation(
        client, ["5581987415206@s.whatsapp.net"], "2026-07-31T14:30:44+00:00")

    assert found is not None
    assert found["created_at"] == "2026-07-31T18:00:00+00:00"


@pytest.mark.asyncio
async def test_receipt_with_caption_blocks_the_cancellation():
    """Legenda + comprovante — o formato real que os webhooks gravam."""
    client, table = _client(receipt_rows=[{
        "phone": "5581987415206",
        "content": "Pagamento ok\n[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00.",
        "created_at": "2026-07-31T17:27:29+00:00",
    }])
    now = datetime(2026, 7, 31, 20, 15, tzinfo=TZ)

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Raphaelle"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, _appt(created_at="2026-07-31T14:30:44+00:00"), None, now)

    mock_wpp.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
async def test_clinic_is_emailed_only_once_per_appointment():
    """Este cron roda a cada 30 minutos e a pendência só sai com ação humana. A
    dedup lia `events.data` — coluna que não existe (log_event grava em
    `metadata`) — e por isso NUNCA acusava aviso anterior: a clínica recebia o
    mesmo e-mail a cada meia hora."""
    client, _ = _client(receipt_rows=_RECEIPT_ROW, event_rows=[{"id": "ev-1"}])
    now = datetime(2026, 7, 31, 20, 45, tzinfo=TZ)

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Raphaelle"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
        await spr._cancel_unpaid_appointment(client, _appt(created_at="2026-07-31T14:30:44+00:00"), None, now)

    mock_email.assert_not_awaited()
    mock_log_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_lookup_filters_by_appointment_id_in_the_database():
    """Sem filtrar por appointment_id no banco, um aviso de OUTRA consulta
    silenciaria o e-mail desta."""
    client, _ = _client(receipt_rows=_RECEIPT_ROW)
    events = client.from_("events")
    now = datetime(2026, 7, 31, 20, 45, tzinfo=TZ)

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Raphaelle"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, _appt(created_at="2026-07-31T14:30:44+00:00"), None, now)

    events.contains.assert_called_once_with("metadata", {"appointment_id": "evt-abc"})
