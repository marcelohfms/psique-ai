"""Tests for app/google_sheets.py — Pagamentos row + comprovante hyperlink text.

The comprovante cell (column I) shows a filename as clickable text. That text has
to be the name the file ACTUALLY has in Drive: whoever opens the sheet searches
Drive by that name. Before the shared helper existed, the sheet rebuilt its own
version of the name (comma kept, ".jpg" hardcoded) and it never matched.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.google_drive import build_receipt_filename
from app.google_sheets import append_payment_receipt

DRIVE_LINK = "https://drive.google.com/file/d/abc123/view"


@pytest.fixture(autouse=True)
def _payments_sheet_configured():
    old = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    os.environ["GOOGLE_SHEETS_PAYMENTS_ID"] = "sheet-123"
    yield
    if old is None:
        os.environ.pop("GOOGLE_SHEETS_PAYMENTS_ID", None)
    else:
        os.environ["GOOGLE_SHEETS_PAYMENTS_ID"] = old


async def _append(**overrides):
    """Call append_payment_receipt with the Sheets API stubbed out.

    Returns (mock_append_row, mock_set_hyperlink).
    """
    kwargs = {
        "patient_name": "Maria Silva",
        "phone": "5581999999999@s.whatsapp.net",
        "doctor_name": "Dr. Júlio",
        "appointment_dt": "06/07/2026 09:00",
        "amount": "600,00",
        "drive_link": DRIVE_LINK,
    }
    kwargs.update(overrides)
    with patch("app.google_sheets._credentials", MagicMock()), \
         patch("app.google_sheets.build", MagicMock()), \
         patch("app.google_sheets._append_row_payments", MagicMock(return_value="Pagamentos!A5:J5")) as mock_row, \
         patch("app.google_sheets._set_hyperlink_cell", MagicMock()) as mock_link:
        await append_payment_receipt(**kwargs)
    return mock_row, mock_link


def _hyperlink_filename(mock_link) -> str:
    # _set_hyperlink_cell(service, spreadsheet_id, updated_range, drive_link, filename)
    return mock_link.call_args[0][4]


async def test_comprovante_text_is_the_resolved_drive_filename():
    """When the caller already knows the final Drive name (register_payment gets it
    back from rename_file), the sheet must display it verbatim — real extension
    included, so a PDF receipt is not labelled ".jpg"."""
    _, mock_link = await _append(receipt_filename="Maria_Silva_06-07-2026_R$600-00.pdf")
    assert _hyperlink_filename(mock_link) == "Maria_Silva_06-07-2026_R$600-00.pdf"


async def test_comprovante_text_falls_back_to_shared_helper():
    """Callers without a resolved name (one-off scripts, or a failed Drive rename)
    fall back to the same canonical stem the rename would have used — hyphens in
    the amount, and no invented extension."""
    _, mock_link = await _append()
    filename = _hyperlink_filename(mock_link)
    assert filename == build_receipt_filename("Maria Silva", "06/07/2026 09:00", "600,00")
    assert filename == "Maria_Silva_06-07-2026_R$600-00"
    assert "," not in filename
    assert ".jpg" not in filename


async def test_no_hyperlink_written_without_drive_link():
    _, mock_link = await _append(drive_link="")
    mock_link.assert_not_called()
