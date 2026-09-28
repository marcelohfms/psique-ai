# tests/test_booking_texts.py
from datetime import datetime
from zoneinfo import ZoneInfo

from app.booking_texts import confirmation_text, format_appt_line

TZ = ZoneInfo("America/Recife")


def test_format_appt_line_with_and_without_note():
    start = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
    assert format_appt_line("julio", start) == "Dr. Júlio — segunda-feira, 05/10/2026 às 14:00"
    assert format_appt_line("bruna", start, "Domiciliar") == (
        "Dra. Bruna — segunda-feira, 05/10/2026 às 14:00 (Domiciliar)"
    )


def test_confirmation_text_normal_has_pix_and_2h():
    txt = confirmation_text("normal", "Dr. Júlio — segunda-feira, 05/10/2026 às 14:00", "Carla")
    assert txt.startswith("Consulta registrada! ✅\nDr. Júlio — segunda-feira, 05/10/2026 às 14:00\n\n")
    assert "R$ 100,00 em até 2 horas" in txt
    assert "💳 PIX: 42006848000178" in txt


def test_confirmation_text_variants():
    line = "Dr. Júlio — x"
    assert confirmation_text("cortesia", line, "Carla") == (
        "Perfeito, Carla! 😊 Consulta confirmada:\nDr. Júlio — x\n\n"
        "Como combinado, a taxa de reserva está isenta. Até lá!"
    )
    assert confirmation_text("taxa_isenta", line, "Carla").endswith("A taxa de reserva foi dispensada. Até lá!")
    assert confirmation_text("taxa_paga", line, "Carla").endswith("A taxa de reserva já está paga. Até lá!")


def test_confirmation_text_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        confirmation_text("xyz", "l", "c")


from app.booking_texts import cancel_text, change_text
from app.graph.prompts import CORRECT_PIX_KEY


def test_change_text_clinic():
    t = change_text("clinic", "Carla", "linha velha", "linha nova")
    assert t.startswith("Olá, Carla! A Clínica Psiquê precisou alterar sua consulta.")
    assert "Era: linha velha\nAgora: linha nova" in t
    assert "taxa" not in t


def test_change_text_patient_with_new_fee():
    t = change_text("patient", "", "a", "b", new_fee=True)
    assert t.startswith("Olá! Conforme combinado, sua consulta foi alterada.")
    assert "menos de 24h" in t and "R$ 100,00" in t and CORRECT_PIX_KEY in t


def test_cancel_text_refund_and_plural():
    t = cancel_text("clinic", "Carla", ["l1", "l2"], "devolver")
    assert t.startswith("Olá, Carla! A Clínica Psiquê precisou cancelar suas consultas:\nl1\nl2")
    assert "devolução da taxa" in t


def test_cancel_text_credit():
    t = cancel_text("patient", "Carla", ["l1"], "credito")
    assert "Conforme combinado, sua consulta foi cancelada:\nl1" in t
    assert "fica guardado para a próxima consulta" in t


def test_cancel_text_no_fee_mention_when_retained_or_none():
    for action in ("reter", None):
        t = cancel_text("patient", "Carla", ["l1"], action)
        assert "taxa" not in t
        assert t.endswith("Se quiser marcar uma nova data, é só responder aqui.")
