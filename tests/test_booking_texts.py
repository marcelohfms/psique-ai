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
