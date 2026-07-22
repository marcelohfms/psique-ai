import os
from datetime import date, datetime

import scripts.send_return_reminders as srr

JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"


def _row(**overrides):
    row = {
        "id": "rr1",
        "patient_id": "p1",
        "doctor_id": JULIO_ID,
        "return_interval": "3_meses",
        "next_return_date": "2026-10-13",
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
        "patients": {"name": "João"},
    }
    row.update(overrides)
    return row


def test_pending_template_um_mes_antes():
    result = srr.pending_template(date(2026, 9, 15), _row())
    assert result == ("retorno_um_mes_antes", "month_before_sent_at")


def test_pending_template_no_mes():
    result = srr.pending_template(date(2026, 10, 20), _row())
    assert result == ("retorno_no_mes", "month_of_sent_at")


def test_pending_template_atrasado():
    result = srr.pending_template(date(2026, 11, 1), _row())
    assert result == ("retorno_atrasado", "overdue_sent_at")


def test_pending_template_nada_a_enviar_fora_das_janelas():
    result = srr.pending_template(date(2026, 8, 1), _row())
    assert result is None


def test_pending_template_ja_enviado_nao_repete():
    row = _row(month_before_sent_at="2026-09-01T00:00:00+00:00")
    result = srr.pending_template(date(2026, 9, 15), row)
    assert result is None


def test_pending_template_15_dias_nunca_dispara():
    row = _row(return_interval="15_dias", next_return_date="2026-07-20")
    result = srr.pending_template(date(2026, 7, 20), row)
    assert result is None


def test_pending_template_1_mes_pula_um_mes_antes_via_flag():
    # save_classification já marca month_before_sent_at pra 1_mes no momento
    # da classificação — o cron não precisa de lógica especial, só respeita a flag.
    row = _row(return_interval="1_mes", next_return_date="2026-08-13",
               month_before_sent_at="2026-07-13T00:00:00+00:00")
    result = srr.pending_template(date(2026, 7, 14), row)
    assert result is None  # mês-antes já marcado, e ainda não é agosto (mês do retorno)


def test_pending_template_virada_de_ano_nao_quebra():
    # dezembro/2026 é o mês antes de janeiro/2027 -> não pode comparar só o
    # número do mês (12 != 1 - 1), tem que normalizar por (ano, mês).
    row = _row(next_return_date="2027-01-10")
    result = srr.pending_template(date(2026, 12, 5), row)
    assert result == ("retorno_um_mes_antes", "month_before_sent_at")


from unittest.mock import AsyncMock, MagicMock, patch


def _client_returning(data):
    execute = AsyncMock(return_value=MagicMock(data=data))
    table = MagicMock()
    for m in ("select", "eq", "gt", "limit", "update"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


# ── _has_future_appointment ───────────────────────────────────────────────


async def test_has_future_appointment_true_quando_existe():
    client, _ = _client_returning([{"id": "a9"}])
    out = await srr._has_future_appointment(client, "p1", JULIO_ID, "2026-07-13T00:00:00+00:00")
    assert out is True


async def test_has_future_appointment_false_quando_vazio():
    client, _ = _client_returning([])
    out = await srr._has_future_appointment(client, "p1", JULIO_ID, "2026-07-13T00:00:00+00:00")
    assert out is False


# ── _send_for_row ─────────────────────────────────────────────────────────


async def test_send_for_row_envia_a_todos_contatos_consulta_e_marca_flag():
    client, table = _client_returning([])
    contacts = [{"phone": "5581111", "name": "João"}, {"phone": "5581222", "name": "Mãe"}]
    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    assert mock_send.await_count == 2
    table.update.assert_called_once()


async def test_send_for_row_inclui_contatos_pausados():
    # Acesso a medicação controlada depende de retorno em dia — pausa do bot
    # não deve silenciar o lembrete (mesmo padrão de
    # send_appointment_reminders.py / app/patients.py::get_contacts_for_patient).
    client, _ = _client_returning([])
    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[]) as mock_get_contacts, \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock):
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_get_contacts.assert_awaited_once_with("p1", "consulta", include_inactive=True)


async def test_send_for_row_sem_contato_nao_envia_nem_marca():
    client, table = _client_returning([])
    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[]), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_send.assert_not_awaited()
    table.update.assert_not_called()


async def test_send_for_row_marca_flag_mesmo_se_um_contato_falhar():
    client, table = _client_returning([])
    contacts = [{"phone": "5581111", "name": "João"}, {"phone": "5581222", "name": "Mãe"}]

    async def flaky(phone, *a, **k):
        if phone == "5581111":
            raise RuntimeError("falha transitória")

    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               side_effect=flaky):
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    table.update.assert_called_once()


# ── main ─────────────────────────────────────────────────────────────────


async def test_main_pula_linha_com_consulta_futura_do_mesmo_medico():
    # next_return_date no mesmo mês de "hoje" (mockado) -> pending_template
    # garantidamente dá match em retorno_no_mes; o único motivo pra
    # _send_for_row não ser chamado deve ser o skip por consulta futura.
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=[_row(next_return_date="2026-09-13")]))

    appt_table = MagicMock()
    for m in ("select", "eq", "gt", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = AsyncMock(return_value=MagicMock(data=[{"id": "a-future"}]))

    def from_(table_name):
        return rr_table if table_name == "return_reminders" else appt_table

    client = MagicMock()
    client.from_.side_effect = from_

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_return_reminders._send_for_row",
               new_callable=AsyncMock) as mock_send, \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        with patch("scripts.send_return_reminders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 15, tzinfo=srr.TZ)
            await srr.main()

    mock_send.assert_not_awaited()


async def test_main_processa_em_lotes_com_pausa():
    rows = [_row(id=f"rr{i}", next_return_date="2026-10-13") for i in range(12)]
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=rows))

    appt_table = MagicMock()
    for m in ("select", "eq", "gt", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = AsyncMock(return_value=MagicMock(data=[]))  # sem consulta futura

    def from_(table_name):
        return rr_table if table_name == "return_reminders" else appt_table

    client = MagicMock()
    client.from_.side_effect = from_

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_return_reminders._send_for_row",
               new_callable=AsyncMock) as mock_send, \
         patch("scripts.send_return_reminders.asyncio.sleep",
               new_callable=AsyncMock) as mock_sleep, \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        # força "hoje" pra dentro da janela retorno_um_mes_antes (12 candidatos).
        # Só `.now` é sobrescrito — `.fromisoformat` continua a implementação
        # real (usada por pending_template via `date.fromisoformat`, que não é
        # afetado por este patch pois é um símbolo separado).
        with patch("scripts.send_return_reminders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 15, tzinfo=srr.TZ)
            await srr.main()

    assert mock_send.await_count == 12  # 12 linhas, todas elegíveis (nada enviado ainda)
    mock_sleep.assert_awaited_once_with(srr.BATCH_PAUSE_SECONDS)  # 2 lotes de 10 -> 1 pausa
