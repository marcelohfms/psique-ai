from unittest.mock import AsyncMock

import attendant_db
import payments
from payments import _calc_valor_consulta

JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
BRUNA_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"


def _appt(appointment_id, patient_id, patient_name, phone, **overrides):
    row = {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "start_time": "2026-07-10T14:00:00+00:00",
        "doctor_id": "d5baa58b-a788-4f40-b8c0-512c189150be",
        "paid_at": None,
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
        "consultation_type": None,
        "status": "scheduled",
        "patients": {
            "name": patient_name,
            "birth_date": "1990-01-01",
            "custom_price": None,
            "patient_contacts": [
                {"is_self": True, "contacts": {"phone": phone, "name": patient_name}},
            ],
        },
    }
    row.update(overrides)
    return row


# ── compute_pendencias ────────────────────────────────────────────────────────


async def test_compute_pendencias_sem_filtro_retorna_tudo(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000"),
        _appt("a2", "p2", "Maria", "5581999991111"),
    ]
    out = await payments.compute_pendencias(fake_client)
    assert {p["appointment_id"] for p in out} == {"a1", "a2"}
    # cada agendamento sem taxa nem consulta paga gera 2 pendências
    assert len(out) == 4


async def test_compute_pendencias_filtra_por_patient_ids(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000"),
        _appt("a2", "p2", "Maria", "5581999991111"),
    ]
    out = await payments.compute_pendencias(fake_client, patient_ids=["p1"])
    assert {p["appointment_id"] for p in out} == {"a1"}
    assert all(p["paciente"] == "João" for p in out)


async def test_compute_pendencias_patient_ids_vazio_nao_consulta(fake_client):
    fake_client.store["appointments"] = [_appt("a1", "p1", "João", "5581999990000")]
    out = await payments.compute_pendencias(fake_client, patient_ids=[])
    assert out == []


async def test_compute_pendencias_taxa_ja_paga_nao_aparece(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000",
              booking_fee_paid_at="2026-07-01T00:00:00+00:00"),
    ]
    out = await payments.compute_pendencias(fake_client)
    assert {p["tipo"] for p in out} == {"consulta"}


async def test_compute_pendencias_extrai_telefone_do_contato_self(fake_client):
    fake_client.store["appointments"] = [_appt("a1", "p1", "João", "5581999990000")]
    out = await payments.compute_pendencias(fake_client)
    assert all(p["phone"] == "5581999990000" for p in out)


async def test_compute_pendencias_agrupa_primeira_consulta_dividida(fake_client):
    # Menor de idade: 1ª consulta vira 2 linhas (pais + paciente), datas diferentes,
    # mesmo patient_id — deve virar 1 única pendência de "consulta" e 1 de "taxa".
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "Gabriel", "5581999990000",
              consultation_type="primeira_consulta",
              start_time="2026-07-01T09:00:00+00:00"),
        _appt("a2", "p1", "Gabriel", "5581999990000",
              consultation_type="primeira_consulta",
              start_time="2026-07-09T10:00:00+00:00"),
    ]
    out = await payments.compute_pendencias(fake_client)
    consultas = [p for p in out if p["tipo"] == "consulta"]
    taxas = [p for p in out if p["tipo"] == "taxa"]
    assert len(consultas) == 1
    assert len(taxas) == 1
    assert consultas[0]["appointment_id"] == "a1,a2"
    assert "01/07/2026 06:00" in consultas[0]["data_hora"]
    assert "09/07/2026 07:00" in consultas[0]["data_hora"]


async def test_compute_pendencias_nao_agrupa_pacientes_diferentes(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", "5581999990000", consultation_type="primeira_consulta"),
        _appt("a2", "p2", "Maria", "5581999991111", consultation_type="primeira_consulta"),
    ]
    out = await payments.compute_pendencias(fake_client)
    assert {p["appointment_id"] for p in out if p["tipo"] == "consulta"} == {"a1", "a2"}


async def test_compute_pendencias_fallback_telefone_sem_is_self(fake_client):
    appt = _appt("a1", "p1", "João", "5581999990000")
    appt["patients"]["patient_contacts"] = [
        {"is_self": False, "contacts": {"phone": "5581988887777", "name": "Responsável"}},
    ]
    fake_client.store["appointments"] = [appt]
    out = await payments.compute_pendencias(fake_client)
    assert all(p["phone"] == "5581988887777" for p in out)


# ── _calc_valor_consulta ──────────────────────────────────────────────────────


def test_calc_valor_consulta_custom_price_sobrepoe_tudo():
    # custom_price (valor no cartão) vence mesmo com médico/idade/tipo que dariam
    # outro valor — mas o desconto de R$50 dinheiro/PIX ainda se aplica sobre ele.
    assert _calc_valor_consulta(JULIO_ID, "2015-05-01", "primeira_consulta", 999) == 949


def test_calc_valor_consulta_custom_price_courtesy():
    # custom_price=0 é cortesia — sem desconto, retorna 0.
    assert _calc_valor_consulta(JULIO_ID, "2015-05-01", "primeira_consulta", 0) == 0


def test_calc_valor_consulta_dra_bruna():
    assert _calc_valor_consulta(BRUNA_ID, "1990-01-01", None, None) == 650


def test_calc_valor_consulta_julio_pediatrico_primeira_consulta():
    assert _calc_valor_consulta(JULIO_ID, "2015-05-01", "primeira_consulta", None) == 800


def test_calc_valor_consulta_julio_pediatrico_retorno():
    assert _calc_valor_consulta(JULIO_ID, "2015-05-01", "retorno", None) == 700


def test_calc_valor_consulta_doctor_id_desconhecido_usa_fallback():
    assert _calc_valor_consulta("id-inexistente", None, None, None) == 650


# ── mark_paid ───────────────────────────────────────────────────────────────


async def test_mark_paid_taxa_atualiza_booking_fee(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "taxa", 100, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["booking_fee_paid_at"] is not None
    assert "paid_at" not in row


async def test_mark_paid_consulta_atualiza_paid_at(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["paid_at"] is not None


async def test_mark_paid_envia_tipo_e_forma_no_vocabulario_da_planilha(fake_client, monkeypatch):
    # payment_type/payment_method gravados na planilha precisam usar os mesmos
    # rótulos já usados pelo fluxo da Eva ("Taxa de Reserva"/"Consulta", "PIX",
    # "Cartão de Crédito"...) e não os códigos internos (tipo="taxa", forma_pagamento="cartao_credito").
    fake_sheet = AsyncMock()
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", fake_sheet)
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "cartao_credito", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    _, kwargs = fake_sheet.call_args
    assert kwargs["payment_type"] == "Consulta"
    assert kwargs["payment_method"] == "Cartão de crédito"


async def test_mark_paid_ids_multiplos_atualiza_todas_as_linhas(fake_client, monkeypatch):
    fake_client.store["appointments"] = [
        {"appointment_id": "a1", "paid_at": None},
        {"appointment_id": "a2", "paid_at": None},
    ]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1,a2", "consulta", 700, "PIX", "Gabriel", "Dr. Júlio",
        "01/07/2026 09:00 + 09/07/2026 10:00", "5581999990000",
    )
    assert all(row["paid_at"] is not None for row in fake_client.store["appointments"])


async def test_mark_paid_sheet_failure_nao_propaga(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(
        payments, "_append_payment_sheet",
        AsyncMock(side_effect=RuntimeError("sheets down")),
    )
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    monkeypatch.setattr(attendant_db, "log_event", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["paid_at"] is not None  # gravação principal não foi afetada pela falha da planilha


async def test_mark_paid_sheet_failure_alerta_clinica_e_loga_evento(fake_client, monkeypatch):
    # Falha silenciosa na planilha não pode mais passar despercebida: precisa
    # gerar um evento de auditoria e um e-mail de alerta específico p/ a clínica
    # lançar manualmente (caso Matheus Silva Mônica Lopes, 2026-07-15).
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(
        payments, "_append_payment_sheet",
        AsyncMock(side_effect=RuntimeError("sheets down")),
    )
    fake_log = AsyncMock()
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    fake_email = AsyncMock()
    monkeypatch.setattr(payments, "_send_clinic_email", fake_email)
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    fake_log.assert_awaited_once_with("payment_sheet_append_failed", "5581999990000", {
        "appointment_id": "a1", "paciente": "João", "tipo": "consulta", "valor": 700,
    })
    alert_call = next(
        c for c in fake_email.await_args_list if "FALHA" in c.kwargs.get("subject", c.args[0] if c.args else "")
    )
    assert "João" in alert_call.kwargs.get("body", alert_call.args[1] if len(alert_call.args) > 1 else "")


async def test_mark_paid_sheet_ok_nao_alerta(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    fake_log = AsyncMock()
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    fake_log.assert_not_awaited()


async def test_mark_paid_email_failure_nao_propaga(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(
        payments, "_send_clinic_email",
        AsyncMock(side_effect=RuntimeError("email down")),
    )
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["paid_at"] is not None  # gravação principal não foi afetada pela falha do e-mail


async def test_mark_paid_com_comprovante_fica_visivel_em_find_receipts(fake_client, monkeypatch):
    # Simetria: um comprovante anexado pela atendente no dashboard deve virar a MESMA
    # linha "[imagem]: COMPROVANTE DE PAGAMENTO... [drive_link:URL]" no histórico, de modo
    # que find_receipts (que só lê a tabela `messages`) o enxergue como se tivesse vindo
    # da conversa — marcado como registrado pela atendente.
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    monkeypatch.setattr(attendant_db, "log_event", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "taxa", 150, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
        drive_link="https://drive.google.com/file/d/abc/view",
    )
    receipts = await payments.find_receipts(fake_client, "5581999990000")
    assert len(receipts) == 1
    assert receipts[0]["drive_link"] == "https://drive.google.com/file/d/abc/view"
    assert "registrado pela atendente" in receipts[0]["descricao"]


async def test_mark_paid_com_drive_link_envia_email_comprovante_recebido(fake_client, monkeypatch):
    # Quando há comprovante (drive_link), o e-mail à clínica deve seguir o MESMO padrão
    # usado quando o comprovante chega pela conversa (register_payment): assunto
    # "Comprovante recebido — {paciente}" e corpo "💰 Comprovante recebido!...".
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    fake_email = AsyncMock()
    monkeypatch.setattr(payments, "_send_clinic_email", fake_email)
    monkeypatch.setattr(attendant_db, "log_event", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "taxa", 150, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
        drive_link="https://drive.google.com/file/d/abc/view",
    )
    receipt_call = next(
        c for c in fake_email.await_args_list
        if c.kwargs.get("subject", "").startswith("Comprovante recebido")
    )
    assert receipt_call.kwargs["subject"] == "Comprovante recebido — João"
    body = receipt_call.kwargs["body"]
    assert "💰 Comprovante recebido!" in body
    assert "Paciente: João" in body
    assert "Valor: R$ 150" in body
    assert "Tipo: Taxa de Reserva" in body
    assert "Consulta: 10/07/2026 14:00" in body
    assert "Link: https://drive.google.com/file/d/abc/view" in body


async def test_mark_paid_sem_drive_link_mantem_email_pagamento_registrado(fake_client, monkeypatch):
    # Sem comprovante (dinheiro/cartão presencial), não há "comprovante recebido" —
    # mantém o e-mail genérico já existente ("Pagamento registrado pelo dashboard").
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    fake_email = AsyncMock()
    monkeypatch.setattr(payments, "_send_clinic_email", fake_email)
    await payments.mark_paid(
        fake_client, "a1", "taxa", 150, "dinheiro", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    assert not any(
        c.kwargs.get("subject", "").startswith("Comprovante recebido")
        for c in fake_email.await_args_list
    )
    generic_call = next(
        c for c in fake_email.await_args_list
        if c.kwargs.get("subject", "").startswith("Pagamento registrado")
    )
    assert "Pagamento registrado pelo dashboard" in generic_call.kwargs["body"]


async def test_mark_paid_sem_drive_link_nao_grava_mensagem(fake_client, monkeypatch):
    # Pagamento presencial sem comprovante (dinheiro/cartão) não deve poluir o histórico
    # com uma mensagem de comprovante inexistente.
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_paid_at": None}]
    monkeypatch.setattr(payments, "_append_payment_sheet", AsyncMock())
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    monkeypatch.setattr(attendant_db, "log_event", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "taxa", 150, "dinheiro", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
        drive_link="",
    )
    assert fake_client.store.get("messages", []) == []


# ── mark_fee_waived ───────────────────────────────────────────────────────────


async def test_mark_fee_waived_seta_booking_fee_waived(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_waived": False}]
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_fee_waived(fake_client, "a1", "João", "Dr. Júlio", "10/07/2026 14:00")
    row = fake_client.store["appointments"][0]
    assert row["booking_fee_waived"] is True


async def test_mark_fee_waived_ids_multiplos_atualiza_todas_as_linhas(fake_client, monkeypatch):
    fake_client.store["appointments"] = [
        {"appointment_id": "a1", "booking_fee_waived": False},
        {"appointment_id": "a2", "booking_fee_waived": False},
    ]
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_fee_waived(fake_client, "a1,a2", "Gabriel", "Dr. Júlio", "01/07/2026 09:00")
    assert all(row["booking_fee_waived"] is True for row in fake_client.store["appointments"])


async def test_mark_fee_waived_email_failure_nao_propaga(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "booking_fee_waived": False}]
    monkeypatch.setattr(
        payments, "_send_clinic_email",
        AsyncMock(side_effect=RuntimeError("email down")),
    )
    await payments.mark_fee_waived(fake_client, "a1", "João", "Dr. Júlio", "10/07/2026 14:00")
    row = fake_client.store["appointments"][0]
    assert row["booking_fee_waived"] is True


# ── find_receipts ─────────────────────────────────────────────────────────────


def _msg(phone, content, created_at):
    return {"phone": phone, "content": content, "created_at": created_at}


async def test_find_receipts_extrai_descricao_e_drive_link(fake_client):
    fake_client.store["messages"] = [
        _msg("5581999990000",
             "[imagem]: COMPROVANTE DE PAGAMENTO: valor R$ 100,00 [drive_link:https://drive.google.com/file/d/abc/view]",
             "2026-07-01T19:06:45+00:00"),
    ]
    out = await payments.find_receipts(fake_client, "5581999990000")
    assert len(out) == 1
    assert out[0]["drive_link"] == "https://drive.google.com/file/d/abc/view"
    assert "COMPROVANTE DE PAGAMENTO" in out[0]["descricao"]


async def test_find_receipts_ignora_mensagens_sem_comprovante(fake_client):
    fake_client.store["messages"] = [
        _msg("5581999990000", "Obrigada!", "2026-07-01T10:00:00+00:00"),
        _msg("5581999990000", "[imagem]: LAUDO: texto do laudo", "2026-07-01T11:00:00+00:00"),
    ]
    out = await payments.find_receipts(fake_client, "5581999990000")
    assert out == []


async def test_find_receipts_ordena_mais_recente_primeiro(fake_client):
    fake_client.store["messages"] = [
        _msg("5581999990000",
             "[imagem]: COMPROVANTE DE PAGAMENTO: taxa R$ 100,00 [drive_link:https://drive.google.com/file/d/old/view]",
             "2026-07-01T10:00:00+00:00"),
        _msg("5581999990000",
             "[imagem]: COMPROVANTE DE PAGAMENTO: saldo R$ 550,00 [drive_link:https://drive.google.com/file/d/new/view]",
             "2026-07-03T15:26:00+00:00"),
    ]
    out = await payments.find_receipts(fake_client, "5581999990000")
    assert len(out) == 2
    assert out[0]["drive_link"] == "https://drive.google.com/file/d/new/view"
    assert out[1]["drive_link"] == "https://drive.google.com/file/d/old/view"


async def test_find_receipts_busca_variante_sem_o_9(fake_client):
    # Mensagem gravada com o telefone sem o 9º dígito (legado)
    fake_client.store["messages"] = [
        _msg("558199990000",
             "[imagem]: COMPROVANTE DE PAGAMENTO: valor R$ 100,00 [drive_link:https://drive.google.com/file/d/abc/view]",
             "2026-07-01T19:06:45+00:00"),
    ]
    out = await payments.find_receipts(fake_client, "5581999990000")
    assert len(out) == 1
    assert out[0]["drive_link"] == "https://drive.google.com/file/d/abc/view"


# ── find_receipts_for_patient ───────────────────────────────────────────────────


async def test_find_receipts_for_patient_busca_em_todos_os_contatos(fake_client):
    # A pendência mostra o telefone do contato is_self (ex: o próprio paciente
    # menor de idade), mas o comprovante pode ter sido enviado pelo número do
    # responsável — precisa varrer TODOS os contatos vinculados ao paciente
    # (caso Matheus Silva Mônica Lopes / mãe Mayri, 2026-07-17).
    fake_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contacts": {"phone": "5581996746040"}},  # próprio paciente — sem comprovante
        {"patient_id": "p1", "contacts": {"phone": "5581988851971"}},  # mãe — enviou o comprovante
        {"patient_id": "p2", "contacts": {"phone": "5581900000000"}},  # outro paciente — não deve entrar
    ]
    fake_client.store["messages"] = [
        _msg("5581988851971",
             "[imagem]: COMPROVANTE DE PAGAMENTO: valor R$ 550,00 [drive_link:https://drive.google.com/file/d/mae/view]",
             "2026-07-15T20:00:00+00:00"),
    ]
    out = await payments.find_receipts_for_patient(fake_client, "p1")
    assert len(out) == 1
    assert out[0]["drive_link"] == "https://drive.google.com/file/d/mae/view"


async def test_find_receipts_for_patient_sem_contatos_retorna_vazio(fake_client):
    fake_client.store["patient_contacts"] = []
    out = await payments.find_receipts_for_patient(fake_client, "p1")
    assert out == []


async def test_find_receipts_for_patient_dedupe_drive_link_entre_contatos(fake_client):
    # Se o mesmo comprovante (drive_link) aparecer nas mensagens de mais de um
    # contato do paciente (ex: reencaminhado), não deve duplicar no resultado.
    fake_client.store["patient_contacts"] = [
        {"patient_id": "p1", "contacts": {"phone": "5581999990000"}},
        {"patient_id": "p1", "contacts": {"phone": "5581988880000"}},
    ]
    fake_client.store["messages"] = [
        _msg("5581999990000",
             "[imagem]: COMPROVANTE DE PAGAMENTO: valor R$ 100,00 [drive_link:https://drive.google.com/file/d/abc/view]",
             "2026-07-01T19:06:45+00:00"),
        _msg("5581988880000",
             "[imagem]: COMPROVANTE DE PAGAMENTO: valor R$ 100,00 [drive_link:https://drive.google.com/file/d/abc/view]",
             "2026-07-01T19:10:00+00:00"),
    ]
    out = await payments.find_receipts_for_patient(fake_client, "p1")
    assert len(out) == 1


# ── upload_comprovante ───────────────────────────────────────────────────────


async def test_upload_comprovante_preserva_extensao_do_mimetype(monkeypatch):
    # Bug: o arquivo era criado no Drive sem extensão nenhuma, diferente de todo
    # o resto do sistema (register_payment sempre preserva .jpg/.pdf — ver skill
    # payment-receipt-drive-format).
    captured = {}

    def fake_sync(filename, file_bytes, mimetype):
        captured["filename"] = filename
        return "https://drive.google.com/file/d/xyz/view"

    monkeypatch.setattr(payments, "_upload_comprovante_sync", fake_sync)
    await payments.upload_comprovante("João Silva", "10/07/2026 14:00", "550", b"data", "image/jpeg")
    assert captured["filename"] == "João_Silva_10-07-2026_R$550.jpg"


async def test_upload_comprovante_pdf(monkeypatch):
    captured = {}

    def fake_sync(filename, file_bytes, mimetype):
        captured["filename"] = filename
        return "https://drive.google.com/file/d/xyz/view"

    monkeypatch.setattr(payments, "_upload_comprovante_sync", fake_sync)
    await payments.upload_comprovante("João Silva", "10/07/2026 14:00", "550", b"data", "application/pdf")
    assert captured["filename"] == "João_Silva_10-07-2026_R$550.pdf"


async def test_upload_comprovante_mimetype_desconhecido_usa_jpg(monkeypatch):
    captured = {}

    def fake_sync(filename, file_bytes, mimetype):
        captured["filename"] = filename
        return "https://drive.google.com/file/d/xyz/view"

    monkeypatch.setattr(payments, "_upload_comprovante_sync", fake_sync)
    await payments.upload_comprovante("João Silva", "10/07/2026 14:00", "550", b"data", "application/octet-stream")
    assert captured["filename"].endswith(".jpg")
