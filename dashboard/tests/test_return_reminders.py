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
