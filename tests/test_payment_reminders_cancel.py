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
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
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
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
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
async def test_receipt_lookup_falls_back_to_unreadable_event():
    """Sem a linha "[imagem]:" em messages, o evento payment_receipt_unreadable
    (comprovante recebido, mas valor ilegível) ainda segura o cancelamento — caso
    Fernanda Danielle 5587996373892, cujo comprovante virou evento mas não ficou em
    messages."""
    client, _ = _client(receipt_rows=[], event_rows=[
        {"phone": "5587996373892", "created_at": "2026-09-01T18:42:30+00:00"},
    ])
    found = await spr.find_receipt_in_conversation(
        client, ["5587996373892@s.whatsapp.net"], "2026-09-01T16:00:00+00:00",
    )
    assert found is not None
    assert found["created_at"] == "2026-09-01T18:42:30+00:00"
    assert found.get("lookup_failed") is not True


@pytest.mark.asyncio
async def test_receipt_lookup_none_without_message_or_event():
    """Sem comprovante em messages e sem evento de ilegível → nada segura o cron."""
    client, _ = _client(receipt_rows=[], event_rows=[])
    found = await spr.find_receipt_in_conversation(
        client, ["5587996373892@s.whatsapp.net"], "2026-09-01T16:00:00+00:00",
    )
    assert found is None


@pytest.mark.asyncio
async def test_cancel_blocked_when_unreadable_event_exists():
    """Comprovante recebido mas ILEGÍVEL (evento), sem linha em messages: o cron NÃO
    cancela nem mexe no Calendar — segura até a atendente lançar manualmente."""
    client, table = _client(receipt_rows=[], event_rows=[
        {"phone": "5587996373892", "created_at": "2026-09-01T18:42:30+00:00"},
    ])
    now = datetime(2026, 9, 1, 20, 15, tzinfo=TZ)
    appt = _appt(created_at="2026-09-01T16:10:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5587996373892", "name": "Fernanda"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_wpp.assert_not_awaited()
    mock_cal.assert_not_awaited()
    table.update.assert_not_called()


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


# ── Guarda: comprovante na conversa bloqueia também o LEMBRETE de cobrança ───
# Caso Isadora de Sousa Costa (5581988417858, 07/08/2026): a paciente enviou o
# comprovante às 15:01, Eva não chamou register_payment, e às 17:04 este cron
# cobrou a taxa que já tinha sido paga ("De novo?"). O lembrete precisa da mesma
# guarda que o cancelamento: comprovante na conversa → não cobrar, avisar a clínica.


@pytest.mark.asyncio
async def test_reminder_blocked_when_receipt_found_in_conversation():
    """Com comprovante na conversa, o cron NÃO envia o lembrete de cobrança e NÃO
    marca payment_reminder_sent_at — avisa a clínica para registrar na mão."""
    client, table = _client(receipt_rows=_RECEIPT_ROW)
    now = datetime(2026, 8, 7, 17, 4, tzinfo=TZ)
    appt = _appt(created_at="2026-08-07T14:58:26+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Janaina"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
        await spr._send_payment_reminder(client, appt, None, now)

    mock_wpp.assert_not_awaited()
    table.update.assert_not_called()
    mock_email.assert_awaited_once()
    assert "bloquead" in mock_email.await_args.args[0].lower()
    mock_log_event.assert_awaited_once()
    assert mock_log_event.await_args.args[0] == "payment_reminder_blocked_receipt_found"


@pytest.mark.asyncio
async def test_reminder_sent_when_no_receipt_in_conversation():
    """Sem comprovante na conversa, o lembrete segue normalmente e
    payment_reminder_sent_at é marcado."""
    client, table = _client()
    now = datetime(2026, 8, 7, 17, 4, tzinfo=TZ)
    appt = _appt(created_at="2026-08-07T14:58:26+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Janaina"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._send_payment_reminder(client, appt, None, now)

    mock_wpp.assert_awaited_once()
    table.update.assert_called_once()


# ── Guarda: paciente cortesia (custom_price == 0) nunca é cobrado nem cancelado ─
# Caso Lucas Raphael de Oliveira Pereira e Silva (contato Silvana, 5581973460726,
# 11/08/2026): paciente cortesia (patients.custom_price == 0) foi agendado com
# booking_fee_waived=False, o cron cobrou a taxa de reserva ("a vaga só fica
# garantida após o pagamento") e ficou a ~35min de auto-cancelar a consulta.
# Cortesia não deve nenhuma taxa — o cron precisa pular tanto o lembrete quanto o
# cancelamento, independentemente de booking_fee_waived.


@pytest.mark.asyncio
async def test_reminder_skipped_for_courtesy_patient():
    """Paciente cortesia (custom_price == 0): nenhum lembrete de cobrança, nenhum
    marca de payment_reminder_sent_at, e nem sequer buscamos os contatos."""
    client, table = _client()
    now = datetime(2026, 8, 11, 12, 55, tzinfo=TZ)
    appt = _appt(patients={"name": "Lucas Raphael", "custom_price": 0})

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock) as mock_contacts, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp:
        await spr._send_payment_reminder(client, appt, None, now)

    mock_contacts.assert_not_awaited()
    mock_wpp.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_skipped_for_courtesy_patient():
    """Paciente cortesia (custom_price == 0): o cron não cancela a consulta, não
    mexe no Calendar e não notifica ninguém."""
    client, table = _client()
    now = datetime(2026, 8, 11, 14, 55, tzinfo=TZ)
    appt = _appt(patients={"name": "Lucas Raphael", "custom_price": 0})

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock) as mock_contacts, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal:
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_contacts.assert_not_awaited()
    mock_wpp.assert_not_awaited()
    mock_cal.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_deferred_when_receipt_lookup_fails():
    """Se a busca pelo comprovante falhar, o lembrete é adiado para a próxima
    execução (sem e-mail — diferente do cancelamento, adiar uma cobrança em 30min
    é inofensivo e não precisa de intervenção humana)."""
    client, table = _client()
    table.execute = AsyncMock(side_effect=Exception("supabase down"))
    now = datetime(2026, 8, 7, 17, 4, tzinfo=TZ)
    appt = _appt(created_at="2026-08-07T14:58:26+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581987415206", "name": "Janaina"}]), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
        await spr._send_payment_reminder(client, appt, None, now)

    mock_wpp.assert_not_awaited()
    table.update.assert_not_called()
    mock_email.assert_not_awaited()


# ── Janela de 24h do WhatsApp ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_window_open_true_when_recent_inbound():
    """Se o contato mandou mensagem dentro da janela, a conversa está aberta."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[{"created_at": "2026-08-14T11:00:00+00:00"}]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    assert await spr._window_open(client, "5581987415206@s.whatsapp.net", now) is True


@pytest.mark.asyncio
async def test_window_closed_when_no_recent_inbound():
    """Sem mensagem recente do contato, a janela está fechada."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    assert await spr._window_open(client, "5581987415206@s.whatsapp.net", now) is False


@pytest.mark.asyncio
async def test_window_uses_24h_cutoff_and_role_user():
    """O corte é now-24h (UTC) e só conta mensagem inbound (role='user')."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)  # 12:30 UTC

    await spr._window_open(client, "5581987415206@s.whatsapp.net", now)

    table.gte.assert_called_once_with("created_at", "2026-08-13T12:30:00+00:00")
    table.eq.assert_any_call("role", "user")


@pytest.mark.asyncio
async def test_window_checks_both_phone_variants():
    """Reaproveita _phone_variants: casa mensagem gravada com/sem o 9º dígito."""
    client, table = _client()
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    await spr._window_open(client, "5581987415206@s.whatsapp.net", now)

    variants = table.in_.call_args[0][1]
    assert "5581987415206" in variants
    assert "558187415206" in variants


@pytest.mark.asyncio
async def test_window_closed_on_lookup_error():
    """Fail-safe: erro na consulta => tratar como fechada (força template)."""
    client, table = _client()
    table.execute = AsyncMock(side_effect=Exception("supabase down"))
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    assert await spr._window_open(client, "5581987415206@s.whatsapp.net", now) is False


# ── Referência da consulta (próprio-paciente vs responsável) ─────────────────

def test_consulta_ref_reminder():
    assert spr._consulta_ref("reminder", None) == "sua consulta"
    assert spr._consulta_ref("reminder", "Bento") == "a consulta de Bento"


def test_consulta_ref_cancel():
    assert spr._consulta_ref("cancel", None) == "da sua consulta"
    assert spr._consulta_ref("cancel", "Bento") == "da consulta de Bento"


# ── Envio de template via Chatwoot ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_template_builds_expected_payload():
    """_send_template resolve a conversa e chama send_template_message com o
    template, categoria UTILITY, idioma pt_BR e os 4 params posicionais."""
    body_params = {"1": "Mariana", "2": "a consulta de Bento", "3": "Dr. Júlio", "4": "27/08/2026 às 14:00"}

    with patch("app.chatwoot.find_or_create_conversation",
               new_callable=AsyncMock, return_value=4321) as mock_conv, \
         patch("app.chatwoot.send_template_message", new_callable=AsyncMock) as mock_tpl:
        await spr._send_template("5581999767413", spr.TEMPLATE_REMINDER, body_params, "texto livre de fallback")

    mock_conv.assert_awaited_once_with("5581999767413@s.whatsapp.net")
    mock_tpl.assert_awaited_once_with(
        4321,
        template_name=spr.TEMPLATE_REMINDER,
        language="pt_BR",
        category="UTILITY",
        body_params=body_params,
        content="texto livre de fallback",
    )


# ── Roteamento híbrido texto-livre / template ────────────────────────────────

@pytest.mark.asyncio
async def test_notify_uses_free_text_when_window_open():
    """Janela aberta: manda texto livre (send_whatsapp), não usa template."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl:
        ok = await spr._notify(client, "5581999767413", kind="reminder", free_text="oi livre",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is True
    mock_wpp.assert_awaited_once_with("5581999767413", "oi livre")
    mock_tpl.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_uses_template_when_window_closed():
    """Janela fechada: manda template com params corretos, não texto livre."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl:
        ok = await spr._notify(client, "5581999767413", kind="cancel", free_text="cancel livre",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is True
    mock_wpp.assert_not_awaited()
    mock_tpl.assert_awaited_once_with(
        "5581999767413",
        spr.TEMPLATE_CANCEL,
        {"1": "Mariana", "2": "da consulta de Bento", "3": "Dr. Júlio", "4": "27/08/2026 às 14:00"},
        "cancel livre",
    )


@pytest.mark.asyncio
async def test_notify_self_patient_uses_sua_consulta():
    """Contato é o próprio paciente (patient_first=None) => {{2}} = 'sua consulta'."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl:
        await spr._notify(client, "5581996503841", kind="reminder", free_text="x",
                          contact_first="João", patient_first=None,
                          doctor_label="Dra. Bruna", date_str="28/08/2026 às 10:00", now=now)

    assert mock_tpl.await_args.args[2]["2"] == "sua consulta"


@pytest.mark.asyncio
async def test_notify_returns_false_on_send_failure():
    """Falha no envio (ex.: template ainda não aprovado) => retorna False, para o
    guard do cancelamento adiar em vez de cancelar silenciosamente."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template",
               new_callable=AsyncMock, side_effect=Exception("template not approved")):
        ok = await spr._notify(client, "5581999767413", kind="cancel", free_text="x",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is False


@pytest.mark.asyncio
async def test_notify_logs_exception_type_when_str_is_empty(capsys):
    """Algumas exceções (ex.: timeouts do httpx) têm str(e) vazio: sem logar o TIPO,
    o cron só imprimiria 'FALHOU para <phone>: ' e a causa real sumiria. Deve logar
    o nome da classe da exceção mesmo quando a mensagem é vazia."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    class _TimeoutSemMensagem(Exception):
        pass

    assert str(_TimeoutSemMensagem()) == ""  # reproduz o cenário: str(e) vazio

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template",
               new_callable=AsyncMock, side_effect=_TimeoutSemMensagem()):
        ok = await spr._notify(client, "5581999767413", kind="cancel", free_text="x",
                               contact_first="Mariana", patient_first="Bento",
                               doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)

    assert ok is False
    out = capsys.readouterr().out
    assert "_TimeoutSemMensagem" in out  # o TIPO aparece mesmo com str(e) vazio


@pytest.mark.asyncio
async def test_notify_raises_on_invalid_kind():
    """kind inesperado deve falhar alto (ValueError), não cair silenciosamente no
    caminho de cancelamento."""
    client, _ = _client()
    now = datetime(2026, 8, 14, 9, 30, tzinfo=TZ)

    with patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=True), \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock):
        with pytest.raises(ValueError):
            await spr._notify(client, "5581999767413", kind="lembrete_errado", free_text="x",
                              contact_first="Mariana", patient_first="Bento",
                              doctor_label="Dr. Júlio", date_str="27/08/2026 às 14:00", now=now)


@pytest.mark.asyncio
async def test_reminder_uses_template_out_of_window():
    """Fora da janela de 24h, o lembrete de cobrança vai por template (entregável)
    e payment_reminder_sent_at é marcado."""
    client, table = _client()
    now = datetime(2026, 8, 12, 12, 27, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._send_payment_reminder(client, appt, None, now)

    mock_tpl.assert_awaited_once()
    assert mock_tpl.await_args.args[1] == spr.TEMPLATE_REMINDER
    mock_wpp.assert_not_awaited()
    table.update.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_uses_template_out_of_window_and_still_cancels():
    """Fora da janela de 24h, o aviso de cancelamento vai por template (entregável)
    e a consulta é cancelada normalmente — sem cair no cancelamento silencioso."""
    client, table = _client()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template", new_callable=AsyncMock) as mock_tpl, \
         patch("scripts.send_payment_reminders.send_whatsapp", new_callable=AsyncMock) as mock_wpp, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_tpl.assert_awaited_once()
    assert mock_tpl.await_args.args[1] == spr.TEMPLATE_CANCEL
    mock_wpp.assert_not_awaited()
    mock_cal.assert_awaited_once()
    table.update.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_deferred_when_template_send_fails():
    """Se o template ainda não existe/aprova, o envio falha, ninguém é notificado e
    a consulta NÃO é cancelada (adiada) — em vez de cancelar em silêncio."""
    client, table = _client()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template",
               new_callable=AsyncMock, side_effect=Exception("template not approved")), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock) as mock_cal, \
         patch("app.database.log_event", new_callable=AsyncMock) as mock_log_event:
        await spr._cancel_unpaid_appointment(client, appt, None, now)

    mock_cal.assert_not_awaited()
    table.update.assert_not_called()
    mock_log_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_deferred_does_not_touch_checkpoint():
    """Envio não entregue (template falhou) => cancelamento adiado e o checkpoint
    NÃO é tocado — senão 'sua vaga foi liberada' entraria no histórico com a
    consulta ainda ativa, repetindo a cada run."""
    client, table = _client()
    now = datetime(2026, 8, 14, 12, 0, tzinfo=TZ)
    appt = _appt(created_at="2026-08-12T09:20:00+00:00")
    graph = MagicMock()

    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new_callable=AsyncMock,
               return_value=[{"phone": "5581996503841", "name": "Arthur"}]), \
         patch("scripts.send_payment_reminders._window_open", new_callable=AsyncMock, return_value=False), \
         patch("scripts.send_payment_reminders._send_template",
               new_callable=AsyncMock, side_effect=Exception("template not approved")), \
         patch("scripts.send_payment_reminders.save_to_checkpoint", new_callable=AsyncMock) as mock_ckpt, \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock):
        await spr._cancel_unpaid_appointment(client, appt, graph, now)

    mock_ckpt.assert_not_awaited()
    table.update.assert_not_called()
# ── Task 6: lembrete/cancelamento vão só p/ o contato que fez a reserva ────────
# _window_open=True em todos: força o caminho de texto livre (send_whatsapp, que
# está mockado) em vez do template real; o foco destes testes é QUAIS contatos
# recebem, não o canal.

def _pay_appt(**kw):
    base = {
        "appointment_id": "evt-1", "start_time": "2026-09-01T14:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "created_at": "2026-08-01T00:00:00+00:00",
        "patient_id": "p-joao", "contact_id": "c-reserva",
        "patients": {"name": "João Silva", "custom_price": 200},
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_payment_reminder_goes_to_booking_contact_only():
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João Silva"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders._window_open", new=AsyncMock(return_value=True)), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value={"phone": "5581000", "name": "João Silva"})), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()) as sw:
        await spr._send_payment_reminder(client, _pay_appt(), None, datetime.now(TZ))
    assert [c.args[0] for c in sw.await_args_list] == ["5581000"]


@pytest.mark.asyncio
async def test_payment_reminder_fallback_when_no_contact_id():
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders._window_open", new=AsyncMock(return_value=True)), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()) as sw:
        await spr._send_payment_reminder(client, _pay_appt(contact_id=None), None, datetime.now(TZ))
    assert sorted(c.args[0] for c in sw.await_args_list) == ["5581000", "5581999"]


@pytest.mark.asyncio
async def test_receipt_guard_still_scans_all_financial_contacts():
    # comprovante enviado por um contato que NÃO é o da reserva ainda bloqueia.
    client = MagicMock()
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders._window_open", new=AsyncMock(return_value=True)), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)) as frc, \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value={"phone": "5581000", "name": "João"})), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()):
        await spr._send_payment_reminder(client, _pay_appt(), None, datetime.now(TZ))
    # a guarda recebeu AMBOS os telefones financeiros, não só o da reserva
    assert sorted(frc.await_args.args[1]) == ["5581000", "5581999"]


@pytest.mark.asyncio
async def test_cancel_goes_to_booking_contact_only():
    # o cancelamento notifica só o contato da reserva, não a mãe.
    client = MagicMock()
    table = MagicMock()
    table.update.return_value = table
    table.eq.return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João Silva"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders._window_open", new=AsyncMock(return_value=True)), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)), \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value={"phone": "5581000", "name": "João Silva"})), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()) as sw:
        await spr._cancel_unpaid_appointment(client, _pay_appt(), None, datetime.now(TZ))
    # só o telefone da reserva foi notificado — a mãe (5581999) NÃO.
    assert [c.args[0] for c in sw.await_args_list] == ["5581000"]


@pytest.mark.asyncio
async def test_cancel_receipt_guard_still_scans_all_financial_contacts():
    # a guarda de comprovante do cancelamento continua varrendo TODOS os contatos.
    client = MagicMock()
    with patch("scripts.send_payment_reminders.get_financial_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João"},
                                           {"phone": "5581999", "name": "Mãe"}])), \
         patch("scripts.send_payment_reminders._window_open", new=AsyncMock(return_value=True)), \
         patch("scripts.send_payment_reminders.find_receipt_in_conversation",
               new=AsyncMock(return_value=None)) as frc, \
         patch("scripts.send_payment_reminders.get_contact_by_id",
               new=AsyncMock(return_value={"phone": "5581000", "name": "João"})), \
         patch("scripts.send_payment_reminders.cancel_calendar_event", new_callable=AsyncMock), \
         patch("app.database.log_event", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock), \
         patch("scripts.send_payment_reminders.send_whatsapp", new=AsyncMock()):
        await spr._cancel_unpaid_appointment(client, _pay_appt(), None, datetime.now(TZ))
    assert sorted(frc.await_args.args[1]) == ["5581000", "5581999"]


# ── Janela de envio (8h–21h, horário de Recife) ───────────────────────────────

@pytest.mark.parametrize(
    "hour, expected",
    [
        (7, False),   # antes da janela (madrugada/manhã cedo)
        (8, True),    # abre exatamente às 8h
        (12, True),   # meio do dia
        (20, True),   # último horário dentro da janela
        (21, False),  # fecha às 21h (limite exclusivo)
        (23, False),  # noite
        (2, False),   # madrugada
    ],
)
def test_within_send_window_boundaries(hour, expected):
    now = datetime(2026, 9, 2, hour, 30, tzinfo=TZ)
    assert spr._within_send_window(now) is expected


def test_within_send_window_converts_from_utc():
    # 23:30 UTC == 20:30 em Recife (UTC-3) → dentro da janela.
    now_utc = datetime(2026, 9, 2, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert spr._within_send_window(now_utc) is True
