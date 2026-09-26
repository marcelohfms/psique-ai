import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import scripts.complete_appointments as ca

NOW_ISO = "2026-07-23T19:34:58+00:00"


def _appt(**kw):
    base = {
        "id": "row-1",
        "appointment_id": "evt-abc",
        "patient_id": "p-natalia",
        "patients": {"name": "Natalia Pimentel"},
        "confirmed_at": None,
        "reminder_day_before_sent_at": None,
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _own_ids():
    # Por padrão nenhum contato é do próprio paciente (evita Supabase real).
    with patch("scripts.complete_appointments.own_contact_ids",
               new=AsyncMock(return_value=set())) as m:
        yield m


@pytest.fixture(autouse=True)
def _events():
    # Por padrão ninguém recebeu o pedido de avaliação ainda (primeira vez).
    with patch("scripts.complete_appointments.get_events_by_type",
               new=AsyncMock(return_value=[])) as get_ev, \
         patch("scripts.complete_appointments.log_event",
               new=AsyncMock()) as log_ev:
        yield get_ev, log_ev


def _client(future_data=None):
    execute = AsyncMock(return_value=MagicMock(data=future_data or []))
    table = MagicMock()
    for m in ("select", "update", "eq", "gt", "limit"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


@pytest.mark.asyncio
async def test_sends_pos_consulta_for_same_day_booking_without_confirmation():
    # Regression (Natalia, 5581996332827): appointment booked and held the
    # same day never gets a day-before reminder, so confirmed_at is always
    # null even though the patient attended. Must NOT be treated as a no-show.
    client, table = _client()
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581996332827"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_send.assert_awaited_once_with("5581996332827", "Natalia", third_party=True, returning=False)
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_skips_when_day_before_reminder_sent_and_never_confirmed():
    # Real no-show/cancel signal preserved: a day-before reminder DID ask for
    # confirmation and the patient never replied.
    client, table = _client()
    appt = _appt(reminder_day_before_sent_at="2026-07-21T10:00:00+00:00")
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock) as mock_gcfp, \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, appt, NOW_ISO)
    mock_gcfp.assert_not_awaited()
    mock_send.assert_not_awaited()
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_sends_when_confirmed_regardless_of_reminder():
    client, table = _client()
    appt = _appt(reminder_day_before_sent_at="2026-07-21T10:00:00+00:00",
                 confirmed_at="2026-07-21T12:00:00+00:00")
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, appt, NOW_ISO)
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_future_appointment_exists():
    client, table = _client(future_data=[{"id": "row-2"}])
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock) as mock_gcfp, \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_gcfp.assert_not_awaited()
    mock_send.assert_not_awaited()
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_skips_when_no_consulta_contact():
    client, table = _client()
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_send.assert_not_awaited()
    table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_skips_when_appointment_classified_as_alta():
    # Alta (discharge): the doctor classified this consultation as alta, so
    # the pos-consulta is not sent.
    execute = AsyncMock(return_value=MagicMock(data=[]))
    appt_table = MagicMock()
    for m in ("select", "update", "eq", "gt", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = execute

    rr_table = MagicMock()
    for m in ("select", "eq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=[{
        "return_interval": "alta",
        "last_classified_appointment_id": "evt-abc",
    }]))

    client = MagicMock()
    client.from_.side_effect = lambda t: rr_table if t == "return_reminders" else appt_table

    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock) as mock_gcfp, \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_gcfp.assert_not_awaited()
    mock_send.assert_not_awaited()
    appt_table.update.assert_called_once_with({"pos_consulta_sent_at": NOW_ISO})


@pytest.mark.asyncio
async def test_sends_when_alta_row_is_for_a_different_appointment():
    # A return_reminders alta row for a *different* consultation must not
    # suppress the pos-consulta of this one.
    execute = AsyncMock(return_value=MagicMock(data=[]))
    appt_table = MagicMock()
    for m in ("select", "update", "eq", "gt", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = execute

    rr_table = MagicMock()
    for m in ("select", "eq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=[{
        "return_interval": "alta",
        "last_classified_appointment_id": "evt-other",
    }]))

    client = MagicMock()
    client.from_.side_effect = lambda t: rr_table if t == "return_reminders" else appt_table

    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_pos_consulta_usa_regra_da_idade_sem_fan_out():
    # A escolha de destinatário (idade + manual_hold) é responsabilidade de
    # consultation_reminder_contacts, que recebe a consulta inteira. O script
    # não faz fan-out cru pros contatos "consulta": a pós-consulta de um adulto
    # não vaza pra família.
    client, table = _client()
    crc = AsyncMock(return_value=[{"phone": "5581111"}])
    with patch("scripts.complete_appointments.consultation_reminder_contacts", crc), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(appointment_id="evt-xyz"), NOW_ISO)
    mock_send.assert_awaited_once_with("5581111", "Natalia", third_party=True, returning=False)
    args, kwargs = crc.await_args
    assert args[0] == "p-natalia"
    assert args[1]["appointment_id"] == "evt-xyz"
    assert kwargs.get("include_inactive") is False


def test_should_skip_unconfirmed():
    assert ca._should_skip_unconfirmed(
        {"reminder_day_before_sent_at": "2026-07-21T10:00:00+00:00", "confirmed_at": None}
    )
    assert not ca._should_skip_unconfirmed(
        {"reminder_day_before_sent_at": None, "confirmed_at": None}
    )
    assert not ca._should_skip_unconfirmed(
        {"reminder_day_before_sent_at": "2026-07-21T10:00:00+00:00", "confirmed_at": "2026-07-21T12:00:00+00:00"}
    )


@pytest.mark.asyncio
async def test_pos_consulta_escolhe_versao_paciente_ou_terceiro(_own_ids):
    # Contato do próprio paciente recebe "avaliacao_google"; qualquer outro
    # (mãe de menor, cônjuge que agendou) recebe a versão de terceiro.
    _own_ids.return_value = {"c-self"}
    client, _ = _client()
    crc = AsyncMock(return_value=[
        {"id": "c-self", "phone": "5581111"},
        {"id": "c-mae", "phone": "5582222"},
    ])
    with patch("scripts.complete_appointments.consultation_reminder_contacts", crc), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    assert mock_send.await_args_list[0].args == ("5581111", "Natalia")
    assert mock_send.await_args_list[0].kwargs == {"third_party": False, "returning": False}
    assert mock_send.await_args_list[1].args == ("5582222", "Natalia")
    assert mock_send.await_args_list[1].kwargs == {"third_party": True, "returning": False}


@pytest.mark.asyncio
async def test_pos_consulta_usa_versao_retorno_quando_ja_pediu_avaliacao(_events):
    # Já pedimos avaliação a esse telefone para esse paciente → versão
    # "retorno". Pedido feito para OUTRO paciente (mãe com dois filhos) não conta.
    get_ev, log_ev = _events
    get_ev.return_value = [{"metadata": {"patient_id": "p-natalia"}}]
    client, _ = _client()
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"id": "c1", "phone": "5581111"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock) as mock_send:
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
        get_ev.return_value = [{"metadata": {"patient_id": "p-irmao"}}]
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    assert mock_send.await_args_list[0].kwargs["returning"] is True
    assert mock_send.await_args_list[1].kwargs["returning"] is False
    get_ev.assert_awaited_with("5581111", ca.AVALIACAO_EVENT)
    assert log_ev.await_args_list[0].args == (
        ca.AVALIACAO_EVENT, "5581111",
        {"patient_id": "p-natalia", "appointment_id": "evt-abc", "returning": True},
    )


@pytest.mark.asyncio
async def test_pos_consulta_nao_registra_evento_se_envio_falha(_events):
    _, log_ev = _events
    client, table = _client()
    with patch("scripts.complete_appointments.consultation_reminder_contacts",
               new_callable=AsyncMock, return_value=[{"phone": "5581111"}]), \
         patch("scripts.complete_appointments.send_pos_consulta",
               new_callable=AsyncMock, side_effect=RuntimeError("chatwoot fora")):
        await ca._process_pos_consulta(client, _appt(), NOW_ISO)
    log_ev.assert_not_awaited()
    table.update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("third_party,returning,template,inicio,fim", [
    (False, False, "avaliacao_google",
     "Oi, Natalia! 😊\n\nEspero que sua consulta na Psiquê", "Muito obrigada!"),
    (True, False, "avaliacao_google_terceiro",
     "Oi! 😊\n\nEspero que a consulta de Natalia na Psiquê", "Muito obrigada!"),
    (False, True, "avaliacao_google_retorno",
     "Oi, Natalia! 😊\n\nEspero que mais essa consulta na Psiquê", "Se você já avaliou, muito obrigada! 💜"),
    (True, True, "avaliacao_google_retorno_terceiro",
     "Oi! 😊\n\nEspero que mais essa consulta de Natalia na Psiquê", "Se você já avaliou, muito obrigada! 💜"),
])
async def test_send_pos_consulta_template_de_avaliacao(third_party, returning, template, inicio, fim):
    send = AsyncMock()
    with patch("app.chatwoot.find_or_create_conversation", AsyncMock(return_value=42)), \
         patch("app.chatwoot.send_template_message", send):
        await ca.send_pos_consulta("5581111", "Natalia", third_party=third_party, returning=returning)
    kwargs = send.await_args.kwargs
    assert send.await_args.args == (42,)
    assert kwargs["template_name"] == template
    assert kwargs["body_params"] == {"1": "Natalia"}
    assert kwargs["content"].startswith(inicio)
    assert kwargs["content"].endswith(fim)
