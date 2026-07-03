from unittest.mock import AsyncMock

import payments


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


async def test_mark_paid_sheet_failure_nao_propaga(fake_client, monkeypatch):
    fake_client.store["appointments"] = [{"appointment_id": "a1", "paid_at": None}]
    monkeypatch.setattr(
        payments, "_append_payment_sheet",
        AsyncMock(side_effect=RuntimeError("sheets down")),
    )
    monkeypatch.setattr(payments, "_send_clinic_email", AsyncMock())
    await payments.mark_paid(
        fake_client, "a1", "consulta", 700, "PIX", "João", "Dr. Júlio",
        "10/07/2026 14:00", "5581999990000",
    )
    row = fake_client.store["appointments"][0]
    assert row["paid_at"] is not None  # gravação principal não foi afetada pela falha da planilha
