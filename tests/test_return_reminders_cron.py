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


def test_pending_template_mes_anterior():
    result = srr.pending_template(date(2026, 9, 15), _row())
    assert result == ("retorno_mes_anterior", "month_before_sent_at")


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


def test_pending_template_alta_nunca_dispara_mesmo_sem_next_return_date():
    row = _row(return_interval="alta", next_return_date=None)
    # alta não tem data — não pode dar crash em date.fromisoformat(None)
    assert srr.pending_template(date(2026, 8, 11), row) is None


def test_pending_template_intervalo_normal_ainda_dispara():
    row = _row(return_interval="1_mes", next_return_date="2026-09-11")
    # agosto é o mês anterior a setembro -> retorno_mes_anterior
    assert srr.pending_template(date(2026, 8, 11), row) == (
        "retorno_mes_anterior", "month_before_sent_at",
    )


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
    assert result == ("retorno_mes_anterior", "month_before_sent_at")


# ── _plain_message ───────────────────────────────────────────────────────


def test_plain_message_self_sauda_pelo_nome_e_fala_seu_retorno():
    msg = srr._plain_message("retorno_mes_anterior", "Maria", "Dr. Júlio")
    assert "Olá, Maria!" in msg
    assert "secretária virtual da Psiquê" in msg
    assert "seu retorno com Dr. Júlio" in msg


def test_plain_message_terceiro_sauda_contato_e_referencia_paciente():
    msg = srr._plain_message("retorno_mes_anterior_terceiro", "Ana", "Dr. Júlio", "Bruno")
    assert "Olá, Ana!" in msg
    assert "o retorno de Bruno com Dr. Júlio" in msg
    assert "Assim Bruno evita ficar sem acesso" in msg
    # não deve sobrar pronome de 2ª pessoa se dirigindo à Ana como se fosse ela a paciente
    assert "seu retorno" not in msg


def test_plain_message_no_mes_terceiro_referencia_paciente_no_corpo():
    msg = srr._plain_message("retorno_no_mes_terceiro", "Ana", "Dra. Bruna", "Bruno")
    assert "Olá, Ana!" in msg
    assert "Bruno está no período indicado" in msg
    assert "Bruno não fique sem acesso" in msg


def test_plain_message_atrasado_terceiro_referencia_paciente_no_corpo():
    msg = srr._plain_message("retorno_atrasado_terceiro", "Ana", "Dr. Júlio", "Bruno")
    assert "Olá, Ana!" in msg
    assert "retorno de Bruno com Dr. Júlio já passou" in msg
    assert "risco de Bruno ficar sem acesso" in msg
    assert "horário para Bruno." in msg


# ── _build_body_params ───────────────────────────────────────────────────
# Testes de regressão pra travar o mapeamento {{N}} -> valor de cada
# template. A Meta não deixa reusar o número de uma variável, então os
# "_terceiro" repetem patient_first_name em números extras ({{4}}, {{5}}) —
# um erro aqui manda o valor errado pra posição errada na mensagem real.


def test_build_body_params_self_sem_variavel_de_paciente():
    params = srr._build_body_params("retorno_mes_anterior", "Maria", "Dr. Júlio", None)
    assert params == {"1": "Maria", "2": "Dr. Júlio"}


def test_build_body_params_mes_anterior_terceiro_repete_paciente_em_4():
    params = srr._build_body_params("retorno_mes_anterior_terceiro", "Ana", "Dr. Júlio", "Bruno")
    assert params == {"1": "Ana", "2": "Bruno", "3": "Dr. Júlio", "4": "Bruno"}


def test_build_body_params_no_mes_terceiro_repete_paciente_em_4():
    params = srr._build_body_params("retorno_no_mes_terceiro", "Ana", "Dra. Bruna", "Bruno")
    assert params == {"1": "Ana", "2": "Bruno", "3": "Dra. Bruna", "4": "Bruno"}


def test_build_body_params_atrasado_terceiro_repete_paciente_em_4_e_5():
    params = srr._build_body_params("retorno_atrasado_terceiro", "Ana", "Dr. Júlio", "Bruno")
    assert params == {"1": "Ana", "2": "Bruno", "3": "Dr. Júlio", "4": "Bruno", "5": "Bruno"}


from unittest.mock import AsyncMock, MagicMock, patch


# ── send_return_reminder_template ────────────────────────────────────────


async def test_send_return_reminder_template_envia_body_params_montados():
    with patch("app.chatwoot.find_or_create_conversation",
               new_callable=AsyncMock, return_value=42) as mock_conv, \
         patch("app.chatwoot.send_template_message",
               new_callable=AsyncMock) as mock_send:
        await srr.send_return_reminder_template(
            "5581111", "retorno_atrasado_terceiro", "Ana", "Dr. Júlio", "Bruno")
    mock_conv.assert_awaited_once_with("5581111@s.whatsapp.net")
    mock_send.assert_awaited_once()
    _, kwargs = mock_send.call_args
    assert kwargs["template_name"] == "retorno_atrasado_terceiro"
    assert kwargs["body_params"] == {"1": "Ana", "2": "Bruno", "3": "Dr. Júlio", "4": "Bruno", "5": "Bruno"}


def _client_returning(data):
    execute = AsyncMock(return_value=MagicMock(data=data))
    table = MagicMock()
    for m in ("select", "eq", "gt", "in_", "order", "limit", "update"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


# ── _is_stale_classification ────────────────────────────────────────────
# Regra: sempre considerar só a consulta agendada/completa mais recente do
# paciente com o médico. Se ela for diferente da última classificada pelo
# médico (last_classified_appointment_id), a linha está desatualizada —
# esperar o médico reclassificar em vez de lembrar com base num
# next_return_date que já não vale mais (caso Mariana Mendonça, consulta em
# 22/07 ainda não reclassificada, gerou 2 lembretes com data de retorno
# obsoleta).


async def test_is_stale_classification_true_quando_ultima_consulta_diverge():
    client, _ = _client_returning([{"appointment_id": "appt-novo"}])
    out = await srr._is_stale_classification(client, "p1", JULIO_ID, "appt-antigo")
    assert out is True


async def test_is_stale_classification_false_quando_bate_com_classificada():
    client, _ = _client_returning([{"appointment_id": "appt-classificada"}])
    out = await srr._is_stale_classification(client, "p1", JULIO_ID, "appt-classificada")
    assert out is False


async def test_is_stale_classification_false_quando_sem_consultas():
    client, _ = _client_returning([])
    out = await srr._is_stale_classification(client, "p1", JULIO_ID, "appt-classificada")
    assert out is False


# ── _send_for_row ─────────────────────────────────────────────────────────


async def test_send_for_row_envia_a_todos_contatos_consulta_e_marca_flag():
    client, table = _client_returning([])
    contacts = [{"phone": "5581111", "name": "João"}, {"phone": "5581222", "name": "Mãe"}]
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    assert mock_send.await_count == 2
    table.update.assert_called_once()


async def test_send_for_row_contato_igual_paciente_usa_template_self():
    # _row() tem patients.name == "João"; contato com o mesmo nome -> é o
    # próprio paciente, usa o template base (sem {{3}}).
    client, _ = _client_returning([])
    contacts = [{"phone": "5581111", "name": "João"}]
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_send.assert_awaited_once_with("5581111", "retorno_no_mes", "João", "Dr. Júlio", None)


async def test_send_for_row_contato_diferente_paciente_usa_template_terceiro():
    # Contato "Mãe" != paciente "João" -> variante _terceiro, com o primeiro
    # nome do paciente como 4º argumento (vira {{3}} no template).
    client, _ = _client_returning([])
    contacts = [{"phone": "5581222", "name": "Carla Souza"}]
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_send.assert_awaited_once_with("5581222", "retorno_no_mes_terceiro", "Carla", "Dr. Júlio", "João")


async def test_send_for_row_sem_nome_de_contato_usa_template_self():
    # Contato sem nome cadastrado -> cai no nome do paciente (_dn(None or
    # patient_name)), então não é tratado como terceiro.
    client, _ = _client_returning([])
    contacts = [{"phone": "5581333", "name": None}]
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_send.assert_awaited_once_with("5581333", "retorno_no_mes", "João", "Dr. Júlio", None)


async def test_send_for_row_inclui_contatos_pausados():
    # Acesso a medicação controlada depende de retorno em dia — pausa do bot
    # não deve silenciar o lembrete (mesmo padrão de
    # send_appointment_reminders.py / app/patients.py::get_contacts_for_patient).
    client, _ = _client_returning([])
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new_callable=AsyncMock, return_value=[]) as mock_get_contacts, \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock):
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_get_contacts.assert_awaited_once_with("p1", "consulta", include_inactive=True)


async def test_send_for_row_sem_contato_nao_envia_nem_marca():
    client, table = _client_returning([])
    with patch("scripts.send_return_reminders.get_reminder_contacts",
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

    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               side_effect=flaky):
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    table.update.assert_called_once()


# ── main ─────────────────────────────────────────────────────────────────


async def test_main_pula_linha_com_classificacao_desatualizada():
    # next_return_date no mesmo mês de "hoje" (mockado) -> pending_template
    # garantidamente dá match em retorno_no_mes; o único motivo pra
    # _send_for_row não ser chamado deve ser o skip por classificação
    # desatualizada (consulta mais recente != last_classified_appointment_id,
    # que aqui é None por padrão em _row()).
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=[_row(next_return_date="2026-09-13")]))

    appt_table = MagicMock()
    for m in ("select", "eq", "in_", "order", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = AsyncMock(return_value=MagicMock(data=[{"appointment_id": "appt-nova"}]))

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


async def test_return_reminder_adult_with_self_only_self():
    row = {
        "id": "rr1", "patient_id": "p-joao",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "patients": {"name": "João Silva"},
    }
    client = MagicMock()
    table = MagicMock()
    for m in ("update", "eq"):
        getattr(table, m).return_value = table
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.from_.return_value = table
    with patch("scripts.send_return_reminders.get_reminder_contacts",
               new=AsyncMock(return_value=[{"phone": "5581000", "name": "João Silva"}])) as grc, \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new=AsyncMock()) as send:
        await srr._send_for_row(client, row, "retorno_no_mes", "month_of_sent_at", None)
    grc.assert_awaited_once_with("p-joao", "consulta", include_inactive=True)
    assert send.await_count == 1


async def test_main_processa_um_por_vez_com_pausa():
    rows = [_row(id=f"rr{i}", next_return_date="2026-10-13") for i in range(12)]
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=rows))

    appt_table = MagicMock()
    for m in ("select", "eq", "in_", "order", "limit"):
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
        # força "hoje" pra dentro da janela retorno_mes_anterior (12 candidatos).
        # Só `.now` é sobrescrito — `.fromisoformat` continua a implementação
        # real (usada por pending_template via `date.fromisoformat`, que não é
        # afetado por este patch pois é um símbolo separado).
        with patch("scripts.send_return_reminders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 15, tzinfo=srr.TZ)
            await srr.main()

    assert mock_send.await_count == 12  # 12 linhas, todas elegíveis (nada enviado ainda)
    # BATCH_SIZE=1 -> pausa entre CADA envio individual: 11 pausas para 12 candidatos
    # (nenhuma pausa depois do último, pra não atrasar o fim do run à toa).
    assert mock_sleep.await_count == 11
    mock_sleep.assert_awaited_with(srr.BATCH_PAUSE_SECONDS)


async def test_main_ordena_por_dia_do_mes_da_ultima_consulta():
    """Um paciente de retorno longo (6 meses) cuja última consulta caiu no
    fim do mês não deve furar a fila na frente de quem precisa agendar logo
    no início do mês — a ordem de envio segue o dia-do-mês de
    next_return_date (= dia-do-mês da última consulta, preservado por
    _add_months), não a ordem de leitura da tabela."""
    rows = [
        _row(id="rr-fim-mes", return_interval="6_meses", next_return_date="2026-10-28"),
        _row(id="rr-inicio-mes", return_interval="1_mes", next_return_date="2026-10-03"),
        _row(id="rr-meio-mes", return_interval="3_meses", next_return_date="2026-10-15"),
    ]
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=rows))

    appt_table = MagicMock()
    for m in ("select", "eq", "in_", "order", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = AsyncMock(return_value=MagicMock(data=[]))  # sem consulta futura

    def from_(table_name):
        return rr_table if table_name == "return_reminders" else appt_table

    client = MagicMock()
    client.from_.side_effect = from_

    sent_order = []

    async def fake_send_for_row(client, row, template_name, sent_col, graph):
        sent_order.append(row["id"])

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_return_reminders._send_for_row",
               new_callable=AsyncMock) as mock_send, \
         patch("scripts.send_return_reminders.asyncio.sleep", new_callable=AsyncMock), \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        mock_send.side_effect = fake_send_for_row
        # mesmo mês em todas as linhas (só o dia difere) -> as 3 caem em
        # retorno_no_mes simultaneamente, isolando o efeito da ordenação.
        with patch("scripts.send_return_reminders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 10, 10, tzinfo=srr.TZ)
            await srr.main()

    assert sent_order == ["rr-inicio-mes", "rr-meio-mes", "rr-fim-mes"]
