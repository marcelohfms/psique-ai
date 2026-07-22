from datetime import date

import return_reminders as rr


def test_compute_next_return_date_15_dias():
    assert rr.compute_next_return_date(date(2026, 7, 13), "15_dias") == date(2026, 7, 28)


def test_compute_next_return_date_1_mes():
    assert rr.compute_next_return_date(date(2026, 7, 13), "1_mes") == date(2026, 8, 13)


def test_compute_next_return_date_3_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "3_meses") == date(2026, 10, 13)


def test_compute_next_return_date_6_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "6_meses") == date(2027, 1, 13)


def test_compute_next_return_date_dia_31_cai_pro_ultimo_dia_do_mes_curto():
    # 31/01 + 1 mês -> fevereiro só tem 28 dias em 2026 (não bissexto)
    assert rr.compute_next_return_date(date(2026, 1, 31), "1_mes") == date(2026, 2, 28)


def test_compute_next_return_date_interval_invalido():
    import pytest
    with pytest.raises(ValueError):
        rr.compute_next_return_date(date(2026, 7, 13), "2_meses")


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
