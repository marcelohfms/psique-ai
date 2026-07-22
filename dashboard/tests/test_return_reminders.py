from datetime import date

import return_reminders as rr


def test_compute_next_return_date_15_dias():
    assert rr.compute_next_return_date(date(2026, 7, 13), "15_dias") == date(2026, 7, 28)


def test_compute_next_return_date_1_mes():
    assert rr.compute_next_return_date(date(2026, 7, 13), "1_mes") == date(2026, 8, 13)


def test_compute_next_return_date_2_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "2_meses") == date(2026, 9, 13)


def test_compute_next_return_date_3_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "3_meses") == date(2026, 10, 13)


def test_compute_next_return_date_4_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "4_meses") == date(2026, 11, 13)


def test_compute_next_return_date_6_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "6_meses") == date(2027, 1, 13)


def test_compute_next_return_date_dia_31_cai_pro_ultimo_dia_do_mes_curto():
    # 31/01 + 1 mês -> fevereiro só tem 28 dias em 2026 (não bissexto)
    assert rr.compute_next_return_date(date(2026, 1, 31), "1_mes") == date(2026, 2, 28)


def test_compute_next_return_date_interval_invalido():
    import pytest
    with pytest.raises(ValueError):
        rr.compute_next_return_date(date(2026, 7, 13), "5_meses")


# ── _local_date_time ─────────────────────────────────────────────────────


def test_local_date_time_converte_utc_pra_recife():
    # 17:00 UTC = 14:00 em Recife (UTC-3)
    data, hora = rr._local_date_time("2026-07-13T17:00:00+00:00")
    assert data == "2026-07-13"
    assert hora == "14:00"


def test_local_date_time_virada_de_dia_no_fuso_de_recife():
    # 02:30 UTC do dia 13 = 23:30 do dia 12 em Recife — data local é 12, não 13.
    data, hora = rr._local_date_time("2026-07-13T02:30:00+00:00")
    assert data == "2026-07-12"
    assert hora == "23:30"


def test_local_date_time_vazio_sem_start_time():
    assert rr._local_date_time(None) == ("", "")
    assert rr._local_date_time("") == ("", "")


JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
BRUNA_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"


def _appt(appointment_id, patient_id, patient_name, doctor_id=JULIO_ID, **overrides):
    row = {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-07-13T14:00:00+00:00",
        "status": "scheduled",
        "patients": {"name": patient_name},
    }
    row.update(overrides)
    return row


# ── get_today_appointments ────────────────────────────────────────────────


async def test_get_today_appointments_filtra_por_medico_e_dia(fake_client, monkeypatch):
    monkeypatch.setattr(rr, "_TZ", rr._TZ)  # no-op, mantém import explícito
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", doctor_id=JULIO_ID, start_time="2026-07-13T12:00:00+00:00"),
        _appt("a2", "p2", "Maria", doctor_id=BRUNA_ID, start_time="2026-07-13T12:00:00+00:00"),
        _appt("a3", "p3", "Ana", doctor_id=JULIO_ID, start_time="2026-07-14T12:00:00+00:00"),
    ]
    out = await rr.get_today_appointments(fake_client, JULIO_ID, today=date(2026, 7, 13))
    assert {a["appointment_id"] for a in out} == {"a1"}


async def test_get_today_appointments_fuso_horario_na_virada_do_dia(fake_client):
    # 2026-07-13T02:30:00+00:00 = 2026-07-12T23:30:00-03:00 em Recife — ainda
    # é dia 12 no horário local, mesmo com timestamp "13" em UTC. Regression
    # test pro FakeQuery: comparação de string pura (sem parsear o offset)
    # incluiria essa consulta erradamente na janela do dia 13.
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", doctor_id=JULIO_ID, start_time="2026-07-13T02:30:00+00:00"),
    ]
    out = await rr.get_today_appointments(fake_client, JULIO_ID, today=date(2026, 7, 13))
    assert out == []


async def test_get_today_appointments_enriquece_com_data_hora_local(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", doctor_id=JULIO_ID, start_time="2026-07-13T17:00:00+00:00"),
    ]
    out = await rr.get_today_appointments(fake_client, JULIO_ID, today=date(2026, 7, 13))
    assert out[0]["local_date"] == "2026-07-13"
    assert out[0]["local_time"] == "14:00"


# ── get_pending_classification ────────────────────────────────────────────


async def test_get_pending_classification_sem_return_reminders_aparece(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert {a["appointment_id"] for a in out} == {"a1"}


async def test_get_pending_classification_ja_classificada_nao_aparece(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    fake_client.store["return_reminders"] = [
        {"patient_id": "p1", "last_classified_appointment_id": "a1"},
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert out == []


async def test_get_pending_classification_nova_consulta_reabre_pendencia(fake_client):
    # p1 foi classificado com base em a1, mas depois teve a2 (mais recente,
    # ainda não classificada) -> deve reaparecer usando a2.
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-06-01T12:00:00+00:00"),
        _appt("a2", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    fake_client.store["return_reminders"] = [
        {"patient_id": "p1", "last_classified_appointment_id": "a1"},
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert [a["appointment_id"] for a in out] == ["a2"]


async def test_get_pending_classification_ordenado_do_mais_antigo(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-05T12:00:00+00:00"),
        _appt("a2", "p2", "Maria", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert [a["appointment_id"] for a in out] == ["a2", "a1"]


async def test_get_pending_classification_filtra_por_medico(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", doctor_id=JULIO_ID, status="completed"),
        _appt("a2", "p2", "Maria", doctor_id=BRUNA_ID, status="completed"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert {a["appointment_id"] for a in out} == {"a1"}


async def test_get_pending_classification_data_local_na_virada_do_dia(fake_client):
    # Consulta às 22h de Recife (01h UTC do dia seguinte) — a data usada pra
    # classificar (e enviada em data-appointment-date no salvar) precisa ser
    # a data LOCAL da consulta (dia 13), não a data UTC crua (dia 14).
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-14T01:00:00+00:00"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert out[0]["local_date"] == "2026-07-13"


# ── save_classification ───────────────────────────────────────────────────


async def test_save_classification_cria_linha_nova(fake_client):
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="3_meses",
    )
    assert saved["patient_id"] == "p1"
    assert saved["return_interval"] == "3_meses"
    assert saved["next_return_date"] == "2026-10-13"
    assert saved["last_classified_appointment_id"] == "a1"
    assert saved["month_before_sent_at"] is None
    assert fake_client.store["return_reminders"][0]["patient_id"] == "p1"


async def test_save_classification_atualiza_linha_existente(fake_client):
    fake_client.store["return_reminders"] = [{
        "patient_id": "p1", "doctor_id": JULIO_ID, "return_interval": "1_mes",
        "next_return_date": "2026-06-01", "last_classified_appointment_id": "a0",
        "month_before_sent_at": "2026-05-01T00:00:00+00:00",
        "month_of_sent_at": None, "overdue_sent_at": None,
    }]
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="6_meses",
    )
    assert len(fake_client.store["return_reminders"]) == 1  # não duplica linha
    assert saved["return_interval"] == "6_meses"
    assert saved["last_classified_appointment_id"] == "a1"
    # novo ciclo -> flags zeradas
    assert saved["month_before_sent_at"] is None


async def test_save_classification_15_dias_marca_as_3_flags_como_enviadas(fake_client):
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="15_dias",
    )
    assert saved["month_before_sent_at"] is not None
    assert saved["month_of_sent_at"] is not None
    assert saved["overdue_sent_at"] is not None


async def test_save_classification_1_mes_marca_so_month_before_como_enviada(fake_client):
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="1_mes",
    )
    assert saved["month_before_sent_at"] is not None
    assert saved["month_of_sent_at"] is None
    assert saved["overdue_sent_at"] is None


async def test_save_classification_interval_invalido_levanta_erro(fake_client):
    import pytest
    with pytest.raises(ValueError):
        await rr.save_classification(
            fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
            appointment_date=date(2026, 7, 13), return_interval="5_meses",
        )
