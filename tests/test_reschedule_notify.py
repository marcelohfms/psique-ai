import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")


@pytest.mark.asyncio
async def test_format_template_variables():
    """_format_template_vars retorna as 6 variáveis corretas."""
    from scripts.reschedule_notify import _format_template_vars

    original_start = datetime(2026, 6, 13, 10, 0, tzinfo=TZ)
    suggested_start = datetime(2026, 6, 16, 14, 0, tzinfo=TZ)

    result = _format_template_vars(
        patient_name="Ana",
        doctor_label="Dr. Júlio",
        original_start=original_start,
        suggested_start=suggested_start,
    )

    assert result["patient_name"] == "Ana"
    assert result["doctor_label"] == "Dr. Júlio"
    assert "13/06" in result["original_date"]
    assert result["original_time"] == "10h"
    assert "16/06" in result["suggested_date"]
    assert result["suggested_time"] == "14h"


@pytest.mark.asyncio
async def test_send_reschedule_template_calls_send_template():
    """send_reschedule_template chama send_template com as variáveis corretas."""
    from scripts.reschedule_notify import send_reschedule_template

    mock_send = AsyncMock()
    with patch("scripts.reschedule_notify.send_template", mock_send):
        await send_reschedule_template(
            phone="5583999999999",
            patient_name="Ana",
            doctor_label="Dr. Júlio",
            original_date="sexta, 13/06",
            original_time="10h",
            suggested_date="segunda, 16/06",
            suggested_time="14h",
        )

    mock_send.assert_called_once()
    call_args = mock_send.call_args[0]
    assert call_args[0] == "5583999999999"
    assert call_args[1] == "reagendamento"
    assert call_args[2] == "pt_BR"
    components = call_args[3]
    params = components[0]["parameters"]
    assert params[0]["text"] == "Ana"
    assert params[1]["text"] == "Dr. Júlio"
    assert params[2]["text"] == "sexta, 13/06"
    assert params[3]["text"] == "10h"
    assert params[4]["text"] == "segunda, 16/06"
    assert params[5]["text"] == "14h"
