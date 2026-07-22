from datetime import date

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
